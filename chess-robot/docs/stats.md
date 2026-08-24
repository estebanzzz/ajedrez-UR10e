# Estadísticas y supervisión de la feria

> Instructivo de conexión para el sistema externo (ejemplos de código,
> WebSocket en vivo y solución de problemas): [supervision.md](supervision.md).

Todas las partidas quedan registradas en SQLite en la Pi y se exponen por
HTTP/JSON para que el sistema de supervisión (dashboards en otra máquina de la
LAN) las consuma sin tocar la base directamente.

## Dónde viven los datos

`backend/data/scores.db` (configurable con `CHESS_SCORES_DB`), dos tablas:

- **`games`** — solo lo que compite en el ranking/premio diario (ya existía).
- **`game_log`** — el registro completo para supervisión: *todas* las
  partidas, incluidas demos robot vs robot, abandonos (`RESIGNATION`) y
  partidas cortadas por el operador (`ABORTED`). Por partida: inicio/fin,
  duración, modo, jugador, color, dificultad, resultado (POV humano y
  notación de ajedrez), terminación, cantidad de jugadas, **SAN completo**,
  FEN final, puntaje y material capturado.

El deploy nunca pisa `backend/data/` (está excluido en `deploy_to_pi.ps1`);
las tablas se crean solas al arrancar el backend. Para respaldar la base:
`scp esteban@192.168.0.10:robot-ajedrez/chess-robot/backend/data/scores.db .`

## API para los dashboards

CORS habilitado (solo `GET`), así que se puede consultar directo desde el
navegador de otra máquina: `http://192.168.0.10:8000/api/stats/...`

### `GET /api/stats/summary?days=7`

Agregados de los últimos N días (1–365):

```json
{
  "days": 7, "since": "2026-08-18",
  "totals": {"games": 42, "human_games": 35, "demos": 7, "players": 28,
             "avg_duration_s": 310, "avg_moves": 38.5, "top_score": 1810},
  "by_result": {"win": 4, "draw": 2, "loss": 24, "abandoned": 4, "aborted": 1, "demo": 7},
  "by_difficulty": {"principiante": 12, "intermedio": 18, "avanzado": 5},
  "per_day": [{"day": "2026-08-18", "games": 6, "human_games": 5,
               "players": 5, "avg_duration_s": 280}],
  "per_hour": {"10": 3, "11": 8, "15": 12}
}
```

`per_hour` (hora local 00–23) sirve para el gráfico de afluencia del stand.

### `GET /api/stats/games?limit=50&offset=0&day=2026-08-24&mode=human`

Listado paginado, más reciente primero, sin el SAN (liviano). Devuelve
`{"total": N, "games": [...]}`. Filtros opcionales: `day` (YYYY-MM-DD) y
`mode` (`human` | `self_play`).

### `GET /api/stats/games/{id}`

La partida completa, con `san` (jugadas separadas por espacio, listas para
reproducir en un visor) y `final_fen`.

También siguen disponibles `GET /api/ranking` (top del día e histórico) y
`GET /api/game/state` (estado en vivo de la partida actual, el mismo que usa
el kiosk — útil para un panel "ahora jugando").

## Semántica de `result`

POV del humano: `win` / `draw` / `loss` / `abandoned` (tocó "Terminar") /
`timeout` (se le acabó el reloj) / `aborted` (el operador cortó la partida) /
`null` en demos (ver `chess_result` y `termination`). Abandonos y timeouts
puntúan como derrota en el ranking; las abortadas no puntúan.
