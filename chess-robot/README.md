# Chess Robot — Robot Ajedrecista UR10e

Sistema demostrativo para exposición: un UR10e juega al ajedrez contra un humano
sobre un tablero físico sensorizado, controlado por una Raspberry Pi 5.
Especificación completa en [PROYECTO_ROBOT_AJEDREZ.md](../PROYECTO_ROBOT_AJEDREZ.md).

## Estado del proyecto

- ✅ **Fase 1 — Núcleo de juego (sin hardware)**: `game_state`, `move_detector`,
  `engine`, simulador de tablero por consola y tests.
- ✅ **Fase 2 — Tablero sensorizado**: driver de matriz por GPIO directo
  (+ mock de desarrollo), debounce, scanner, WebSocket y diagnóstico visual.
  Falta solo validar con el hardware real cuando exista.
- 🔶 **Fase 3 — Robot y garra**: geometría de calibración, traducción de
  jugadas a pick & place, bandejas y robot simulado listos; pendiente lo que
  requiere hardware (validar `URRtdeRobot`, teach de esquinas, garra real).
- 🔶 **Fase 4 — Integración completa**: orquestador de partida end-to-end
  (sensores → detector → motor → robot) con manejo de errores, verificación
  física de las jugadas del robot y modo resync; API REST + WebSocket de
  partida. Pendiente validar sobre hardware real.
- 🔶 **Fase 5 — UI de exposición**: frontend React kiosk (tablero en vivo,
  barra de evaluación, dificultad, panel de operador oculto) servido en
  `/ui`, más systemd y guía de kiosk para la Pi. Pendiente: puesta a punto
  final sobre el hardware (velocidades, pruebas de estrés).

## Backend — Fase 1

### Instalación

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Linux/Pi: .venv/bin/pip
```

Stockfish es opcional en desarrollo: si no está en el `PATH`, el simulador usa
un motor aleatorio y los tests del motor se saltan. En la Pi se instalará el
binario ARM64.

### Tests

```bash
cd backend
.venv/Scripts/python -m pytest
```

### Simulador de consola

Juega una partida completa usando el mismo camino de detección que usará el
tablero real: cada jugada humana se convierte en la secuencia de snapshots de
sensores (piezas en el aire incluidas) que pasa por el `MoveDetector`.

```bash
cd backend
.venv/Scripts/python -m app.simulator.console --color blanco --nivel intermedio
```

### Diseño clave de la Fase 1

- **Bitmap de ocupación** (`app/board_sensor/bitmap.py`): entero de 64 bits con
  la misma convención que los bitboards de python-chess (`bit 0 = a1`), así el
  bitmap esperado de una posición es directamente `board.occupied`.
- **Identidad por seguimiento de estado** (`app/game_state`): los sensores solo
  ven presencia; partiendo de la posición inicial, cada jugada legal mantiene
  el mapa casilla→pieza. Incluye la base del modo *resync*
  (`mismatched_squares`).
- **Detección de jugadas** (`app/move_detector`): al confirmar, el bitmap final
  se resuelve contra las jugadas legales. Los estados intermedios se validan y
  además **desambiguan capturas**: dos capturas desde el mismo origen dejan el
  mismo bitmap final, pero difieren en qué casilla se vació transitoriamente.
  Las promociones no son distinguibles por bitmap → se reportan candidatas con
  dama por defecto.
- **Motor** (`app/engine`): Stockfish UCI con presets de dificultad
  (`principiante`/`intermedio`/`avanzado`/`maximo` vía `UCI_Elo`) y evaluación
  continua para la barra de la UI. `RandomEngine` como respaldo de desarrollo.

## Backend — Fase 2 (tablero sensorizado)

- **Driver S7 / PLC** (`app/board_sensor/s7.py`) — **el driver de producción**:
  un S7-1200 (CPU 1215C) barre la matriz, lee el botón de confirmación y
  comanda la baliza; la Pi lee el DB por Ethernet con python-snap7
  (`CHESS_DRIVER=s7`, `CHESS_PLC_HOST=<ip>`). Incluye heartbeat de vida del
  PLC y `PanelLink` (botón físico → confirmación; fase de partida → baliza).
  Lado TIA Portal documentado en `docs/plc-s7-1200.md` (DB, SCL, cableado).
- **Driver de matriz GPIO** (`app/board_sensor/matrix_gpio.py`) — respaldo
  sin PLC: matriz directa a los GPIO de la Pi (8 filas + 8 columnas, diodo
  por sensor), acceso real con libgpiod v2. `MockDriver` para desarrollo.
- **`Debouncer` + `BoardScanner`**: barrido a 30 Hz en hilo propio, bitmap
  estable tras N lecturas idénticas, suscriptores y contador de errores I2C.
- **`SensorDetectorBridge`** (`app/move_detector/bridge.py`): conecta el
  scanner con el `MoveDetector` durante el turno humano — el mismo camino
  sirve para mock y hardware real.
- **API** (`app/api/server.py`): `GET /api/status`, WebSocket `/ws/sensors`
  (~20 Hz, solo cambios) y página de **diagnóstico visual** en `/` con el mapa
  de ocupación en vivo (en modo mock, click para simular piezas).

```bash
cd backend
.venv/Scripts/python -m app.api.server    # abre http://localhost:8000
# En la Pi con hardware: CHESS_DRIVER=matrix python -m app.api.server
# Dependencias de la Pi: pip install -r requirements-pi.txt
```

## Backend — Fase 3 (robot y garra)

- **Geometría** (`app/robot_controller/geometry.py`): teach de los centros de
  las 4 esquinas (a1/h1/a8/h8) → las 64 posiciones por interpolación bilineal
  (absorbe inclinación y rotación del tablero). Bandejas en grilla
  (`TrayGrid`) para capturas y reserva de promoción.
- **`RobotController`** (`controller.py`): traduce jugadas UCI a secuencias
  pick & place — aproximación → descenso → garra → altura de tránsito (sobre
  el rey) → traslado. Capturas primero a la bandeja, enroque rey+torre,
  promoción con dama de la reserva. Velocidades reducidas por defecto.
- **`RobotInterface`** (`robot.py`): `SimulatedRobot` (tests/desarrollo) y
  `URRtdeRobot` (ur_rtde real + garra Robotiq por registros del URCap,
  pendiente de validar con el UR10e).
- **Calibración** (`app/calibration/store.py`): persistencia JSON de esquinas,
  bandejas, parámetros por pieza y velocidades —
  ver `config/calibration.example.json`. La rutina asistida de teach
  (freedrive/jog) se completa con el robot real.

## Backend — Fase 4 (integración completa)

- **`GameOrchestrator`** (`app/game_state/orchestrator.py`): máquina de
  estados de la partida — `human_turn` → confirmación → `robot_turn` →
  verificación física contra sensores → vuelta al humano. Jugada ilegal del
  humano → `human_error` con casillas en conflicto; fallo del robot (el
  tablero no refleja su jugada) → `resync` hasta que el operador corrige.
- **API de partida** (`app/api/server.py`): `POST /api/game/new`,
  `POST /api/game/confirm` (botón físico), `POST /api/game/resync-check`,
  `POST /api/game/difficulty`, `GET /api/game/state` y WebSocket `/ws/game`
  con el estado en vivo para la UI de exposición.
- Sin hardware todo corre simulado: driver mock + robot simulado, y el mundo
  virtual se actualiza solo tras cada jugada del robot. En la Pi:
  `CHESS_DRIVER=matrix CHESS_ROBOT_HOST=<ip-del-UR>`.

## Frontend — Fase 5 (UI de exposición)

React + Vite, sin dependencias pesadas (tablero propio renderizado desde el
FEN). Consume `/ws/game` y `/ws/sensors` con reconexión automática.

- **Pantalla pública**: tablero en vivo con última jugada resaltada, barra de
  evaluación, mensajes al público ("Tu turno", "Pensando…", "¡Jaque!"),
  historial SAN y selector de dificultad.
- **Panel de operador oculto** (5 toques rápidos sobre el título): nueva
  partida, confirmar jugada, verificación de resync, mapa de sensores en vivo
  y estado completo. Las casillas en conflicto se resaltan en rojo sobre el
  tablero durante `human_error`/`resync`.

```bash
cd frontend
npm install
npm run build        # el backend sirve dist/ en http://localhost:8000/ui
npm run dev          # desarrollo con hot-reload (proxy al backend en :8000)
```

## Deploy en la Pi (`deploy/`)

- `chess-backend.service`: systemd con arranque automático y restart.
- `kiosk.md`: Chromium en modo kiosk apuntando a `/ui`, pantalla siempre
  encendida y pasos de instalación completos.
