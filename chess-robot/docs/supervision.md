# Conexión del sistema de supervisión

Instructivo para conectar un sistema externo (dashboards, monitoreo) a los
datos del Robot Ajedrecista. Complemento de [stats.md](stats.md), que detalla
el esquema y los ejemplos de respuesta.

## Dónde están los datos

- **Equipo**: Raspberry Pi de la instalación, `192.168.0.10` (red del stand,
  `192.168.0.x`).
- **Servicio**: backend FastAPI en el puerto **8000** (systemd
  `chess-backend`, arranca solo al encender la Pi).
- **Base**: SQLite en `~/robot-ajedrez/chess-robot/backend/data/scores.db`
  (tablas `games` = ranking, `game_log` = todas las partidas).
- **Sin autenticación**: la API es abierta; la protección es que solo se ve
  desde la LAN del stand. No exponer el puerto 8000 a internet.

## Opción A — API REST (recomendada)

CORS ya está habilitado (solo `GET`), así que se puede consumir desde un
navegador, un backend propio, Grafana (datasource JSON), Power BI (origen
web), etc. Documentación interactiva: `http://192.168.0.10:8000/docs`.

| Endpoint | Qué devuelve | Frecuencia sugerida |
| --- | --- | --- |
| `GET /api/stats/summary?days=7` | Agregados: totales, resultados, dificultad, jugadores únicos, serie por día, histograma por hora | cada 30–60 s |
| `GET /api/stats/games?limit=50&offset=0&day=YYYY-MM-DD&mode=human` | Listado paginado de partidas (sin jugadas) | cada 30–60 s |
| `GET /api/stats/games/{id}` | Partida completa: SAN, FEN final, duración, puntaje | bajo demanda |
| `GET /api/ranking?limit=10` | Top del día e histórico (premio de la feria) | cada 30–60 s |
| `GET /api/game/state` | Partida **en vivo**: FEN, turno, evaluación, fase, jugador | 1–2 s (o WebSocket) |
| `WS /ws/game` | Igual que `game/state` pero push: manda un JSON en cada cambio | conexión permanente |

### Prueba de humo

```bash
curl http://192.168.0.10:8000/api/stats/summary?days=7
```

### Python

```python
import requests

BASE = "http://192.168.0.10:8000"

summary = requests.get(f"{BASE}/api/stats/summary", params={"days": 7}, timeout=5).json()
print(summary["totals"], summary["per_hour"])

games = requests.get(f"{BASE}/api/stats/games", params={"limit": 100}, timeout=5).json()
for g in games["games"]:
    print(g["ended_ts"], g["player_name"], g["result"], g["moves"], "jugadas")
```

### JavaScript (navegador, gracias a CORS)

```js
const BASE = 'http://192.168.0.10:8000'
const summary = await fetch(`${BASE}/api/stats/summary?days=7`).then(r => r.json())

// Partida en vivo, en tiempo real:
const ws = new WebSocket(`ws://192.168.0.10:8000/ws/game`)
ws.onmessage = (e) => render(JSON.parse(e.data))  // fen, turn, evaluation, phase…
```

### Semántica de `result` (POV del visitante humano)

`win` · `draw` · `loss` · `abandoned` (tocó "Terminar" en pantalla) ·
`aborted` (el operador cortó la partida) · `null` en demos robot vs robot
(usar `mode`, `chess_result` y `termination`). `termination` agrega el motivo:
`CHECKMATE`, `STALEMATE`, `RESIGNATION`, `ABORTED`, etc.

## Opción B — leer la base SQLite directo

Para análisis offline o importar a otro motor. **No** montar el `.db` por
red (SMB/NFS) ni leerlo en caliente desde otra máquina: SQLite no está hecho
para eso y se arriesga lectura corrupta. Copiar y leer la copia:

```bash
scp esteban@192.168.0.10:robot-ajedrez/chess-robot/backend/data/scores.db ./scores.db
sqlite3 scores.db "SELECT day, COUNT(*) FROM game_log GROUP BY day"
```

Esquema de `game_log` en [stats.md](stats.md). La copia también sirve de
backup diario de la feria.

## Opción C — MQTT hacia Neuronal HUB (tiempo real como "sensores")

El backend puede publicar los valores numéricos de la partida al broker MQTT
de Neuronal HUB (según `REQUISITOS_INTEGRACION_ROBOT_AJEDREZ.md` del equipo
del HUB). Topics en `neuronal/sensors/`, payload = número plano como string,
con `retain` (el HUB recibe el último valor al suscribirse):

| Topic | Valor |
| --- | --- |
| `ajedrez_evaluacion` | Evaluación en peones, positivo = ventaja blancas; mate forzado = ±99 |
| `ajedrez_jugada_numero` | Número de jugada (0 sin partida) |
| `ajedrez_turno` | 0 blancas · 1 negras |
| `ajedrez_fase` | 0 idle · 1 turno humano · 2 error humano · 3 turno robot · 4 resync · 5 fin |
| `ajedrez_partida_activa` | 1 con partida en curso |
| `ajedrez_tiempo_restante` | Segundos del reloj del humano (0 sin reloj) |

Se publica solo lo que cambia (~2 chequeos/s) y todo de nuevo al reconectar.
La conexión reintenta sola (1–30 s): no importa el orden de encendido del
stand.

**Activación** — en `/etc/systemd/system/chess-backend.service` de la Pi,
completar con los datos que pasa el equipo del HUB y reiniciar:

```ini
Environment=CHESS_MQTT_HOST=<IP de la máquina con Neuronal HUB>
Environment=CHESS_MQTT_PORT=1883
Environment=CHESS_MQTT_USERNAME=<usuario>
Environment=CHESS_MQTT_PASSWORD=<contraseña>
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart chess-backend
```

Prueba previa a la feria, desde la Pi: `ping <IP del HUB>` y
`nc -zv <IP del HUB> 1883`; para ver los mensajes,
`mosquitto_sub -h <IP> -u <usuario> -P <contraseña> -t 'neuronal/sensors/#' -v`.

## Si algo no responde

1. ¿Llega la red? `ping 192.168.0.10` (hay que estar en la LAN del stand).
2. ¿El servicio vive? `ssh esteban@192.168.0.10 systemctl status chess-backend`
   — se relanza solo si se cae; `sudo systemctl restart chess-backend` si hace
   falta.
3. Un `connection refused` de 1–2 s suele ser el servicio reiniciando
   (systemd lo levanta en ~3 s): reintentar.
4. El resto de diagnóstico: `http://192.168.0.10:8000/` (sensores) y
   `journalctl -u chess-backend -n 50`.
