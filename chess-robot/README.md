# Chess Robot — Robot Ajedrecista UR10e

Sistema demostrativo para exposición: un UR10e juega al ajedrez contra un humano
sobre un tablero físico sensorizado, controlado por una Raspberry Pi 5.
Especificación completa en [PROYECTO_ROBOT_AJEDREZ.md](../PROYECTO_ROBOT_AJEDREZ.md).

## Estado del proyecto

- ✅ **Fase 1 — Núcleo de juego (sin hardware)**: `game_state`, `move_detector`,
  `engine`, simulador de tablero por consola y tests.
- ✅ **Fase 2 — Tablero sensorizado**: drivers (MCP23017 / matriz GPIO / mock),
  debounce, scanner, WebSocket y diagnóstico visual. Falta solo validar con el
  hardware real cuando exista.
- ⬜ Fase 3 — Robot y garra (ur_rtde, calibración, pick & place)
- ⬜ Fase 4 — Integración completa
- ⬜ Fase 5 — UI de exposición (React kiosk)

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

- **Drivers** (`app/board_sensor/`): `MCP23017Driver` (4 chips I2C, activo en
  bajo, bus inyectable → testeable sin hardware), `MatrixGPIODriver`
  (alternativa libgpiod v2, pendiente de validar) y `MockDriver` para
  desarrollo.
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
# En la Pi con hardware: CHESS_DRIVER=mcp23017 python -m app.api.server
# Dependencias de la Pi: pip install -r requirements-pi.txt
```
