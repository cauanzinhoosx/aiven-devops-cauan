"""Cliente de partidas do Sport Club Cruzeiro Paulista usando a API pública do ESPN (sem chave).

Endpoint usado (não oficial e não documentado pelo ESPN, pode mudar sem aviso):
    https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/teams/{id}/schedule

Os eventos são convertidos para o mesmo formato que o restante do app já usa
(fixture / teams / goals / league), então app.py e normalize_event() seguem iguais.
"""
import datetime
import os
import requests

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"
LEAGUE = os.environ.get("ESPN_LEAGUE", "bra.1").strip()
LEAGUE_NAME = os.environ.get("ESPN_LEAGUE_NAME", "Brasileirão Série A").strip()
TEAM_ID = os.environ.get("ESPN_TEAM_ID", "2022").strip()  # Cruzeiro no ESPN
SEASON = int(os.environ.get("ESPN_SEASON") or datetime.datetime.now(datetime.timezone.utc).year)
CACHE_HOURS = int(os.environ.get("CACHE_HOURS", 6))
TIMEOUT = 20
HEADERS = {"User-Agent": "cruzeiro-dashboard/1.0 (projeto academico)", "Accept": "application/json"}
# Situações em que o jogo não é nem "recente" nem "próximo".
SKIPPED_STATUS_FLAGS = ("POSTPONED", "CANCELED", "CANCELLED", "ABANDONED", "SUSPENDED")


class FootballAPIError(RuntimeError):
    """Erro de API ou resposta em formato inesperado."""


def _get_schedule():
    url = f"{BASE_URL}/{LEAGUE}/teams/{TEAM_ID}/schedule"
    response = requests.get(url, headers=HEADERS, params={"season": SEASON}, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "events" not in payload:
        keys = list(payload) if isinstance(payload, dict) else type(payload).__name__
        raise FootballAPIError(f"Resposta do ESPN sem o campo 'events'. Recebido: {keys}")
    return payload


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _score(value):
    # No endpoint de schedule o placar costuma vir como {"value": 2.0, "displayValue": "2"};
    # em outros endpoints vem como texto. Aceita os dois formatos.
    if isinstance(value, dict):
        value = value.get("value", value.get("displayValue"))
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _team_name(competitor, fallback):
    team = competitor.get("team") or {}
    return team.get("displayName") or team.get("shortDisplayName") or team.get("name") or fallback


def _adapt_event(event):
    """Converte um evento do ESPN para o formato interno. Retorna None se deve ser ignorado."""
    competition = (event.get("competitions") or [{}])[0]
    status = competition.get("status") or event.get("status") or {}
    status_type = status.get("type") or {}
    status_name = str(status_type.get("name") or "").upper()
    if any(flag in status_name for flag in SKIPPED_STATUS_FLAGS):
        return None

    completed = bool(status_type.get("completed"))
    if completed:
        short, long_name = "FT", "Encerrado"
    elif status_type.get("state") == "pre":
        short, long_name = "NS", "Agendado"
    else:
        return None  # em andamento ou situação desconhecida

    try:
        event_id = int(event.get("id"))
    except (TypeError, ValueError):
        raise ValueError(f"evento sem id numérico: {event.get('id')!r}")

    kickoff = _parse_date(event.get("date") or competition.get("date"))
    if kickoff is None:
        raise ValueError(f"evento {event_id} sem data válida")

    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        raise ValueError(f"evento {event_id} sem mandante/visitante")

    return {
        "fixture": {
            "id": event_id,
            "timestamp": int(kickoff.timestamp()),
            "status": {"short": short, "long": long_name},
        },
        "teams": {
            "home": {"name": _team_name(home, "Mandante")},
            "away": {"name": _team_name(away, "Visitante")},
        },
        "goals": {
            "home": _score(home.get("score")) if completed else None,
            "away": _score(away.get("score")) if completed else None,
        },
        "league": {"name": LEAGUE_NAME},
    }


def _timestamp(event):
    return event["fixture"]["timestamp"]


def fetch_team_events():
    """Busca o calendário da temporada em UMA chamada e separa recentes e próximos."""
    raw_events = _get_schedule().get("events") or []

    adapted, broken = [], []
    for raw in raw_events:
        try:
            event = _adapt_event(raw)
        except ValueError as exc:
            broken.append(str(exc))
            continue
        if event:
            adapted.append(event)

    if raw_events and not adapted and broken:
        raise FootballAPIError(f"Formato inesperado nos eventos do ESPN: {broken[0]}")

    finished = sorted((e for e in adapted if e["fixture"]["status"]["short"] == "FT"),
                      key=_timestamp, reverse=True)
    upcoming = sorted((e for e in adapted if e["fixture"]["status"]["short"] == "NS"),
                      key=_timestamp)
    return finished[:10], upcoming[:5]


def normalize_event(event):
    fixture = event.get("fixture") or {}
    teams = event.get("teams") or {}
    goals = event.get("goals") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    timestamp = fixture.get("timestamp")
    match_date = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc) if timestamp else None
    status_info = fixture.get("status") or {}
    status = status_info.get("long") or status_info.get("short") or "Unknown"
    home_score = goals.get("home")
    away_score = goals.get("away")
    winner = None
    if home_score is not None and away_score is not None and status_info.get("short") in (
        "FT", "AET", "PEN"
    ):
        winner = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
    league = event.get("league") or {}
    return {
        "event_id": fixture.get("id"),
        "tournament": league.get("name", ""),
        "match_date": match_date,
        "status": status,
        "home_team": home.get("name", "Mandante"),
        "away_team": away.get("name", "Visitante"),
        "home_score": home_score,
        "away_score": away_score,
        "winner": winner,
    }