"""Dashboard de partidas e desempenho do Cruzeiro Esporte Clube."""
import datetime
import os
import time
import requests
from flask import Flask, render_template, jsonify
import football_client as football
import db_client as db


app = Flask(__name__)
CACHE_HOURS = int(os.environ.get("CACHE_HOURS", football.CACHE_HOURS))
FAILED_REFRESH_COOLDOWN_MINUTES = int(os.environ.get("FAILED_REFRESH_COOLDOWN_MINUTES", 15))
START_TIME = time.time()
LAST_REFRESH_ATTEMPT = 0.0
TEAM_NAME = "Cruzeiro Esporte Clube"
TEAM_URL = f"https://www.espn.com/soccer/team/_/id/{football.TEAM_ID}/cruzeiro"



def init_tables():
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS football_matches (
                    event_id BIGINT PRIMARY KEY,
                    tournament TEXT,
                    match_date TIMESTAMPTZ,
                    status TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_score INTEGER,
                    away_score INTEGER,
                    winner TEXT,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                CREATE TABLE IF NOT EXISTS visits (
                    id SERIAL PRIMARY KEY,
                    path TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
        conn.commit()



init_tables()



def log_visit(path):
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO visits (path) VALUES (%s);", (path,))
        conn.commit()



def save_events(events):
    normalized = [football.normalize_event(event) for event in events]
    normalized = [event for event in normalized if event["event_id"] is not None]
    if not normalized:
        return 0
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            for event in normalized:
                cur.execute("""
                    INSERT INTO football_matches
                    (event_id, tournament, match_date, status, home_team, away_team,
                     home_score, away_score, winner, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (event_id) DO UPDATE SET
                    tournament=EXCLUDED.tournament, match_date=EXCLUDED.match_date,
                    status=EXCLUDED.status, home_team=EXCLUDED.home_team,
                    away_team=EXCLUDED.away_team, home_score=EXCLUDED.home_score,
                    away_score=EXCLUDED.away_score, winner=EXCLUDED.winner,
                    fetched_at=now();
                """, (
                    event["event_id"], event["tournament"], event["match_date"], event["status"],
                    event["home_team"], event["away_team"], event["home_score"],
                    event["away_score"], event["winner"],
                ))
        conn.commit()
    return len(normalized)



def refresh_events(force=False):
    global LAST_REFRESH_ATTEMPT
    now_epoch = time.time()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(fetched_at) AS last_fetch FROM football_matches;")
            row = cur.fetchone()
    last_fetch = row["last_fetch"] if row else None
    stale = not last_fetch or (
        datetime.datetime.now(datetime.timezone.utc) - last_fetch
    ) > datetime.timedelta(hours=CACHE_HOURS)
    if not force and not stale:
        return {"status": "cached", "saved": 0, "message": "Cache ainda válido."}
    if not force and LAST_REFRESH_ATTEMPT and (
        now_epoch - LAST_REFRESH_ATTEMPT
    ) < FAILED_REFRESH_COOLDOWN_MINUTES * 60:
        return {"status": "cooldown", "saved": 0, "message": "Refresh recente falhou; aguardando cooldown."}

    LAST_REFRESH_ATTEMPT = now_epoch
    recent, upcoming = football.fetch_team_events()
    unique_events = {event.get("fixture", {}).get("id"): event for event in recent + upcoming}
    events = [event for event_id, event in unique_events.items() if event_id]
    saved = save_events(events)
    result = {
        "status": "updated",
        "received_recent": len(recent),
        "received_upcoming": len(upcoming),
        "saved_or_updated": saved,
    }
    app.logger.info("Refresh de dados (ESPN) concluído: %s", result)
    return result



def get_matches(kind):
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            if kind == "recent":
                cur.execute("""
                    SELECT * FROM football_matches
                    WHERE match_date <= now() AND home_score IS NOT NULL AND away_score IS NOT NULL
                    ORDER BY match_date DESC LIMIT 10;
                """)
            else:
                cur.execute("""
                    SELECT * FROM football_matches WHERE match_date > now()
                    ORDER BY match_date ASC LIMIT 5;
                """)
            return cur.fetchall()



def build_dashboard():
    refresh_error = None
    try:
        refresh_result = refresh_events()
    except (requests.RequestException, football.FootballAPIError, ValueError, KeyError) as exc:
        refresh_error = str(exc)
        refresh_result = {"status": "error", "message": refresh_error}
        app.logger.warning("Não foi possível atualizar dados do ESPN: %s", exc)

    recent = get_matches("recent")
    upcoming = get_matches("upcoming")
    wins = draws = losses = goals_for = goals_against = 0
    for match in recent:
        is_home = "cruzeiro" in match["home_team"].lower()
        gf = match["home_score"] if is_home else match["away_score"]
        ga = match["away_score"] if is_home else match["home_score"]
        goals_for += gf or 0
        goals_against += ga or 0
        if gf is not None and ga is not None:
            if gf > ga:
                wins += 1
            elif gf == ga:
                draws += 1
            else:
                losses += 1
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM visits;")
            visits = cur.fetchone()["total"]
    return {
        "recent": recent, "upcoming": upcoming, "matches_count": len(recent),
        "wins": wins, "draws": draws, "losses": losses,
        "goals_for": goals_for, "goals_against": goals_against,
        "goal_difference": goals_for - goals_against,
        "total_visits": visits, "uptime_seconds": int(time.time() - START_TIME),
        "cache_hours": CACHE_HOURS, "team_url": TEAM_URL, "season": football.SEASON,
        "refresh_result": refresh_result, "refresh_error": refresh_error,
        "last_updated": datetime.datetime.now(datetime.timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
    }



@app.route("/")
def dashboard():
    log_visit("/")
    return render_template("dashboard.html", data=build_dashboard())



@app.route("/api/dashboard")
def api_dashboard():
    log_visit("/api/dashboard")
    return jsonify(build_dashboard())



@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        result = refresh_events(force=True)
        if result.get("status") != "updated":
            return jsonify(result), 200
        return jsonify({"status": "ok", **result}), 200
    except (requests.RequestException, football.FootballAPIError, ValueError, KeyError) as exc:
        app.logger.exception("Refresh manual falhou")
        return jsonify({"status": "error", "message": str(exc)}), 502



@app.route("/healthz")
def healthz():
    try:
        with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
        return jsonify({"status": "ok"}), 200
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "detail": str(exc)}), 503



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)