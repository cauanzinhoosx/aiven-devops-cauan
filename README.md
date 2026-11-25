# Dashboard do Cruzeiro — Pipeline DevOps

Dashboard Flask com identidade celeste para acompanhar resultados recentes, próximos jogos e resumo de vitórias, empates, derrotas e gols do Cruzeiro Esporte Clube. Os dados são obtidos pela API pública da ESPN e armazenados em PostgreSQL na Aiven.

## Arquitetura
- **Flask**: dashboard e API (`/api/dashboard`).
- **ESPN**: calendário, resultados e partidas por endpoint público não oficial, sem chave.
- **PostgreSQL/Aiven**: cache de partidas e registro de acessos.
- **Docker + GHCR**: build e publicação da imagem.
- **GitHub Actions**: testes, build/push, deploy no Render e atualização periódica.
- **Terraform**: provisionamento do PostgreSQL na Aiven.

## Configuração no Render
Configure em Environment:
- `AIVEN_DB_URL`: URI de conexão PostgreSQL da Aiven.
- `PORT`: `8080` (se necessário).
- `CACHE_HOURS`: `6`.
- `ESPN_TEAM_ID`: `2022` (Cruzeiro; opcional, padrão).
- `ESPN_LEAGUE`: `bra.1` (Brasileirão Série A; opcional, padrão).
- `ESPN_SEASON`: ano da temporada; por padrão usa o ano atual.
- `FAILED_REFRESH_COOLDOWN_MINUTES`: `15`.

## Fonte dos dados
A aplicação consulta `https://site.api.espn.com/apis/site/v2/sports/soccer/{liga}/teams/{id}/schedule`, usando o ID ESPN `2022` para o Cruzeiro e a liga `bra.1`. O calendário é separado em até 10 partidas recentes e 5 próximas. A ESPN disponibiliza esse endpoint publicamente, mas não o documenta como API estável; ele pode mudar sem aviso.

## Endpoints
- `/`: dashboard visual.
- `/api/dashboard`: dados do dashboard em JSON.
- `/api/refresh` (POST): atualiza partidas e retorna erro HTTP quando a fonte falha.
- `/healthz`: health check do banco e da aplicação.

## Banco de dados
Na inicialização, a aplicação cria `football_matches` e `visits` se não existirem. Não é necessário executar SQL manual.

## CI/CD
1. Push na branch `main`.
2. GitHub Actions valida a sintaxe Python, constrói a imagem Docker e publica no GHCR.
3. Render recebe o deploy hook.
4. O workflow de refresh chama `/api/refresh` a cada 6 horas ou manualmente.

## Diagnóstico
Se o dashboard abrir sem partidas, execute o workflow `refresh-data.yml` manualmente e consulte os logs do Render. Um erro da ESPN faz `/api/refresh` retornar HTTP 502; os jogos já armazenados permanecem disponíveis.
