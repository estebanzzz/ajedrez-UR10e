# Robot Ajedrecista UR10e

Un robot colaborativo **Universal Robots UR10e** juega al ajedrez contra una
persona sobre un tablero físico real: ve las piezas con una cámara cenital,
piensa con Stockfish, mueve las fichas con una garra Robotiq y comenta la
partida en voz alta. Todo lo coordina una **Raspberry Pi 5** que además sirve
la interfaz de la exposición en una pantalla en modo kiosk.

Es un proyecto de demostración pensado para ferias y exposiciones: la prioridad
es la robustez, la recuperación de errores y la seguridad del público.

```
     Cámara Basler (cenital)
              │
              ▼
   ┌───────────────────────┐        RTDE        ┌──────────────────┐
   │  Raspberry Pi 5       │ ─────────────────► │ UR10e + Robotiq  │
   │  FastAPI + WebSocket  │                    └──────────────────┘
   │  OpenCV · Stockfish   │        S7          ┌──────────────────┐
   │  React (kiosk)        │ ◄────────────────► │ PLC S7-1200      │
   └───────────────────────┘                    │ botón · baliza   │
              │                                 └──────────────────┘
              ▼
       Pantalla HDMI (UI del público)
```

## Cómo funciona

1. **Ver.** Una cámara industrial Basler mira el tablero desde arriba. Una
   homografía sobre las 4 esquinas rectifica la imagen y la divide en 64
   celdas; cada celda se clasifica en *vacía* o *con ficha* comparándola
   contra el brillo de su propio fondo, así la lectura es inmune al nivel de
   luz de la sala. El resultado es un **bitmap de ocupación de 64 bits**.
2. **Entender.** La cámara solo aporta presencia, no identidad. El estado de la
   partida se lleva en `python-chess`: partiendo de la posición inicial, cada
   jugada legal actualiza el mapa casilla→pieza. Un detector de jugadas
   interpreta los cambios del bitmap (jugada simple, captura, enroque, en
   passant, promoción) y los valida contra las jugadas legales.
3. **Pensar.** Stockfish por UCI, con dificultad ajustable desde la UI
   (principiante → máximo) y evaluación continua para la barra del tablero.
4. **Mover.** La jugada UCI se traduce a una secuencia de *pick & place*:
   aproximación, descenso, cierre de garra, tránsito por encima de la pieza más
   alta, descenso, apertura, retirada. Las capturas van a una bandeja lateral;
   las promociones toman una dama de la bandeja de reserva.
5. **Hablar.** El robot comenta la partida con audios pregenerados
   (texto a voz), con varias personalidades intercambiables.

Si el tablero real y el estado interno se desincronizan, la UI entra en modo
*resync* y muestra qué casillas no coinciden para que el operador las corrija.

## Estructura del repositorio

```
chess-robot/
├── backend/          # Python 3.11 · FastAPI · OpenCV · python-chess · ur_rtde
│   ├── app/
│   │   ├── vision/           # cámara, homografía, clasificador de casillas
│   │   ├── board_sensor/     # bitmap, debounce, scanner + drivers legados
│   │   ├── game_state/       # partida y seguimiento de identidad de piezas
│   │   ├── move_detector/    # bitmap → jugada
│   │   ├── engine/           # Stockfish (UCI)
│   │   ├── robot_controller/ # jugada → trayectorias del UR10e
│   │   ├── personality/      # frases y voz del robot
│   │   ├── calibration/      # asistente de calibración
│   │   ├── scores/           # ranking de la feria
│   │   └── api/              # REST + WebSocket
│   ├── tests/                # ~20 módulos de tests (pytest)
│   └── config/               # calibración y parámetros
├── frontend/         # React + Vite (tablero, barra de eval, panel de operador)
├── deploy/           # systemd, script de deploy, guía de kiosk
└── docs/             # visión, UR10e, PLC, voz, supervisión, estadísticas
```

## Empezar (sin hardware)

El núcleo de juego corre entero en una PC común: hay drivers simulados de
tablero y de robot, así que se puede jugar una partida completa sin cámara,
sin PLC y sin UR10e.

```bash
cd chess-robot/backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt      # Windows: .venv/Scripts/pip
.venv/bin/python -m pytest                     # suite de tests
.venv/bin/python -m app.simulator.console --color blanco --nivel intermedio
```

Stockfish es opcional en desarrollo: si no está en el `PATH` se usa un motor
aleatorio y los tests del motor se saltan.

Para levantar la API y la UI con el tablero simulado:

```bash
cd chess-robot/backend && .venv/bin/python -m uvicorn app.api.server:app --port 8000
cd chess-robot/frontend && npm install && npm run dev
```

## Documentación

| Documento | Contenido |
|---|---|
| [PROYECTO_ROBOT_AJEDREZ.md](PROYECTO_ROBOT_AJEDREZ.md) | Especificación completa: arquitectura, hardware, seguridad, plan por fases |
| [chess-robot/README.md](chess-robot/README.md) | Guía técnica de cada fase, API, deploy en la Raspberry |
| [docs/vision.md](chess-robot/docs/vision.md) | Calibración de la cámara y entrenamiento del clasificador |
| [docs/ur10e.md](chess-robot/docs/ur10e.md) | Control por RTDE, garra y calibración del robot |
| [docs/plc-s7-1200.md](chess-robot/docs/plc-s7-1200.md) | Panel de operación (botón, baliza, e-stop) por S7 |
| [docs/voice.md](chess-robot/docs/voice.md) | Voz y personalidades del robot |
| [docs/supervision.md](chess-robot/docs/supervision.md) · [docs/stats.md](chess-robot/docs/stats.md) | Conexión de un sistema externo de monitoreo |

## Stack

Python 3.11 · FastAPI · WebSocket · python-chess · Stockfish · OpenCV · pypylon
(Basler) · ur_rtde (UR10e) · python-snap7 (S7-1200) · React 18 · Vite ·
systemd · Raspberry Pi OS 64-bit.

## Notas para quien quiera reproducirlo

- Los valores de red (`192.168.0.x`), las posiciones de calibración y los
  parámetros de la cámara que aparecen en el repo son los de **esta**
  instalación; se sobreescriben con variables de entorno
  (`CHESS_ROBOT_HOST`, `CHESS_PLC_HOST`, `CHESS_DRIVER`, …) y con el asistente
  de calibración.
- No hay credenciales en el repositorio. Las claves de servicios externos se
  leen del entorno (`ELEVENLABS_API_KEY`, `CHESS_MQTT_PASSWORD`) y nunca se
  commitean.
- Los datos de la feria (`backend/data/`, ranking y contactos de los
  jugadores) quedan fuera del control de versiones a propósito.
- La detección por matriz de sensores reed (vía PLC o GPIO) sigue implementada
  como respaldo, pero la solución de producción es la visión artificial.

## Seguridad

El robot opera en modo colaborativo con planos de seguridad configurados en el
UR10e que limitan el volumen de trabajo al tablero y las bandejas, velocidad y
fuerza reducidas, y movimiento **solo** en su turno, nunca con una mano sobre
el tablero. Cualquiera que reproduzca el sistema debe configurar sus propios
límites de seguridad en el robot antes de operarlo con público cerca.
