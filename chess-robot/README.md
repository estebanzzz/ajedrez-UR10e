# Chess Robot — Robot Ajedrecista UR10e

Sistema demostrativo para exposición: un UR10e juega al ajedrez contra un humano
sobre un tablero físico sensorizado, controlado por una Raspberry Pi 5.
Especificación completa en [PROYECTO_ROBOT_AJEDREZ.md](../PROYECTO_ROBOT_AJEDREZ.md).

## Estado del proyecto

- ✅ **Fase 1 — Núcleo de juego (sin hardware)**: `game_state`, `move_detector`,
  `engine`, simulador de tablero por consola y tests.
- ✅ **Fase 2 — Detección del tablero (visión artificial)**: cámara Basler
  (pypylon) + OpenCV — homografía de 4 esquinas, clasificador de casillas
  (vacía/ficha, solo presencia) con entrenamiento auto-etiquetado, `VisionDriver`,
  manejo de oclusión, CLIs de calibración y entrenamiento. Pendiente:
  validar con la cámara montada sobre el tablero real. Los drivers de
  matriz reed (PLC `s7`, GPIO `matrix`) quedan como respaldo legado.
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

## Backend — Fase 2 (detección del tablero por visión)

- **Visión artificial** (`app/vision/`) — **la detección de producción**:
  cámara **Basler** cenital (pypylon) + OpenCV. Homografía de 4 esquinas →
  vista cenital → clasificación de cada casilla en vacía/ficha con
  referencias por casilla y umbral aprendido. `VisionDriver` implementa la
  misma interfaz `SensorDriver` (mismo bitmap de 64 bits) y además expone
  bitmaps por color y detección de oclusión (mano/brazo sobre el tablero →
  mantiene la última lectura estable). Guía completa en `docs/vision.md`.
  - Calibración: `python -m app.vision.calibrate` (click en 4 esquinas).
  - Entrenamiento auto-etiquetado: `python -m app.vision.train` (tablero
    vacío + posición inicial, ~1 minuto, nada se etiqueta a mano).
  - Uso: `CHESS_DRIVER=vision` (cámara con `CHESS_CAMERA=basler` |
    `basler:<serial>` | índice UVC).
- **Driver S7 / PLC** (`app/board_sensor/s7.py`) — legado/respaldo (matriz
  reed): un S7-1200 (CPU 1215C) barre la matriz, lee el botón de
  confirmación y comanda la baliza; la Pi lee el DB por Ethernet con
  python-snap7 (`CHESS_DRIVER=s7`, `CHESS_PLC_HOST=<ip>`). Incluye heartbeat
  y `PanelLink` (botón físico → confirmación; fase → baliza). Lado TIA en
  `docs/plc-s7-1200.md`. El PLC sigue vigente como panel (botón/baliza).
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
# Con la cámara real: CHESS_DRIVER=vision python -m app.api.server
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
- **Demo robot vs robot** (`new_game(self_play=True)`): el robot juega
  ambos bandos con el motor a la dificultad elegida, encadenando jugadas en
  un hilo aparte con una pausa entre ellas (`CHESS_ROBOT_SELF_PLAY_DELAY`,
  1.5 s por defecto). Para que sea dinámica, la cámara solo se usa al
  arrancar (exige las 32 piezas en la posición inicial; si falta alguna,
  `resync` hasta que esté) y el brazo no vuelve a la posición de espera
  entre jugadas (sube a altura segura sobre la última casilla). Si el robot
  no puede ejecutar una jugada → `resync` para colocarla a mano. Reclama
  tablas por triple repetición / 50 jugadas para que la demo termine sola;
  no entra al ranking. Se corta con `stop_game()`.
- **API de partida** (`app/api/server.py`): `POST /api/game/new`
  (`mode: "human" | "self_play"`), `POST /api/game/stop`,
  `POST /api/game/confirm` (botón físico), `POST /api/game/resync-check`,
  `POST /api/game/difficulty`, `GET /api/game/state` y WebSocket `/ws/game`
  con el estado en vivo para la UI de exposición.
- Sin hardware todo corre simulado: driver mock + robot simulado, y el mundo
  virtual se actualiza solo tras cada jugada del robot. En la Pi:
  `CHESS_DRIVER=vision CHESS_ROBOT_HOST=<ip-del-UR>`.

## Frontend — Fase 5 (UI de exposición)

React + Vite, sin dependencias pesadas (tablero propio renderizado desde el
FEN). Consume `/ws/game` y `/ws/sensors` con reconexión automática.

- **Pantalla pública**: tablero en vivo con última jugada resaltada, barra de
  evaluación, mensajes al público ("Tu turno", "Pensando…", "¡Jaque!"),
  historial SAN y selector de dificultad. Desde la pantalla de inicio también
  se lanza la **demo robot vs robot** (botón "Ver al robot jugar contra sí
  mismo"); durante la demo el banner muestra qué bando juega y un botón
  "Detener demo". Durante una partida humana, el botón "🏳 Terminar" (con
  confirmación) abandona: gana el robot pero el puntaje del jugador se
  registra igual (`POST /api/game/resign`). **Reloj de partida** elegible al
  iniciar (3/5/10 min o sin reloj; default 5, `CHESS_ROBOT_GAME_MINUTES`):
  corre solo en el turno del humano y al llegar a cero es derrota por tiempo.
- **Panel de operador oculto** (5 toques rápidos sobre el título): nueva
  partida, confirmar jugada, verificación de resync, mapa de sensores en vivo
  y estado completo. Las casillas en conflicto se resaltan en rojo sobre el
  tablero durante `human_error`/`resync`.
- **Voz y personalidad del robot**: cara animada y globo de diálogo abajo a la
  derecha; el robot comenta la partida y provoca al rival con frases
  pregrabadas (ElevenLabs) y efectos de sonido. Nivel ajustable desde el panel
  de operador (mudo / comentarista / provocador). Guía completa, incluido
  cómo regenerar los audios, en [docs/voice.md](docs/voice.md).

## Estadísticas para supervisión (`/api/stats`)

Todas las partidas (humanas, demos, abandonos y abortadas) quedan en la tabla
`game_log` de `backend/data/scores.db`, con jugadas SAN completas, duración y
resultado. `GET /api/stats/summary`, `/api/stats/games` y
`/api/stats/games/{id}` (CORS solo lectura) alimentan los dashboards del
sistema de supervisión de la feria desde otra máquina de la LAN. Detalle de
esquema y ejemplos en [docs/stats.md](docs/stats.md).

## Menú de navegación

Todas las páginas comparten un menú lateral fijo para saltar entre ellas con el
mouse, sin escribir URLs:

| Destino        | Página                                    |
| -------------- | ----------------------------------------- |
| `/ui`          | Partida (UI de exposición, kiosk)         |
| `/calibracion` | Calibración de visión (esquinas + train)  |
| `/calibration` | Calibración del robot (teach de puntos)   |
| `/`            | Diagnóstico de sensores                   |
| `/docs`        | Documentación REST (Swagger de FastAPI)   |

El botón ☰ lo pliega a una franja de iconos y la preferencia se recuerda
(`localStorage`). En `/ui` arranca plegado para no distraer al público durante
la exposición. Las páginas del backend lo reciben inyectado desde
`app/api/nav.py`; la UI React lo replica en `src/SideNav.jsx` (misma paleta y
mismos destinos).

```bash
cd frontend
npm install
npm run build        # el backend sirve dist/ en http://localhost:8000/ui
npm run dev          # desarrollo con hot-reload (proxy al backend en :8000)
```

## Deploy en la Pi (`deploy/`)

- `deploy_to_pi.ps1`: transferencia completa desde Windows por SSH (ver abajo).
- `chess-backend.service`: systemd con arranque automático y restart.
- `kiosk.md`: Chromium en modo kiosk apuntando a `/ui`, pantalla siempre
  encendida y pasos de instalación completos.

### Transferir el proyecto a la Pi

`deploy_to_pi.ps1` copia todo el árbol por SSH sin pasar por git (van también
los archivos sin commitear: `app/vision/`, la calibración de `config/`, etc.),
buildeando antes el frontend en la PC para no necesitar Node en la Pi.

> **Antes de deployar hay que pushear a GitHub.** Como la copia no pasa por
> git, lo que corre en la Pi puede no existir en ningún otro lado. El orden es
> siempre `git commit` → `git push origin main` → `deploy_to_pi.ps1`
> (ver [Reglas de trabajo](../PROYECTO_ROBOT_AJEDREZ.md#0-reglas-de-trabajo-git-y-deploy)):
>
> ```bash
> git add -A && git commit -m "..." && git push origin main
> ```

```powershell
# Actualizar el código y reiniciar el backend
.\deploy\deploy_to_pi.ps1 -PiHost 192.168.0.10 -Restart

# Pi nueva: además crea el venv, instala dependencias y el servicio systemd
.\deploy\deploy_to_pi.ps1 -PiHost 192.168.0.10 -Install -RobotHost 192.168.0.25
```

Destino por defecto: `~/robot-ajedrez/chess-robot` (lo que apunta el
`WorkingDirectory` del servicio), configurable con `-Dest`. Se excluyen `.git`,
`node_modules`, `.venv`, `__pycache__` y `backend/data/` (los puntajes de la
feria viven solo en la Pi); `backend/config/` se respalda antes de pisarse, o
se conserva del todo con `-KeepConfig`.

Detalles que hacen falta para que la copia funcione desde Windows: el `tar` de
Windows guarda los directorios como `0555` (Windows no tiene permisos POSIX),
así que la extracción usa `--delay-directory-restore` y normaliza los modos en
un staging antes de copiar al destino final.

### La instalación en marcha

| Qué | Dónde |
| --- | --- |
| Pi | `esteban@192.168.0.10`, proyecto en `~/robot-ajedrez/chess-robot` |
| Servicio | `chess-backend` (systemd), `CHESS_DRIVER=vision` |
| Cámara | Basler acA3800-10gm **GigE** en `192.168.0.55` |
| UR10e | `192.168.0.25` (`CHESS_ROBOT_HOST`) |
| Pantalla | Chromium kiosk sobre `/ui` (ver `deploy/kiosk.md`) |

La cámara es de red, no USB: la PC y la Pi la ven por igual, pero **solo una
puede abrirla a la vez** — para que juegue la Pi hay que cerrar el backend de
la PC. El driver reintenta cada 3 s y la toma solo. Detalles en
[docs/vision.md](docs/vision.md).

El PLC S7-1200 (`192.168.0.20`) ya no participa de la detección; con el driver
de visión no se instancia el `PanelLink`, así que la confirmación de jugada es
el botón en pantalla.
