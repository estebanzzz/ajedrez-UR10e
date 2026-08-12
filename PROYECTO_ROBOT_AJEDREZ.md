# Proyecto: Robot Ajedrecista UR10e — Exposición

## 1. Resumen

Sistema demostrativo para exposición en el que un robot colaborativo **Universal Robots UR10e** juega al ajedrez contra un humano sobre un tablero físico sensorizado. El control central es una **Raspberry Pi 5**, que ejecuta el motor de ajedrez, lee la matriz de sensores del tablero, comanda el robot y la garra **Robotiq**, y sirve una interfaz gráfica en pantalla para el público con tablero virtual y evaluación de la partida en tiempo real.

- **Modo de juego:** contra humano, con dificultad ajustable desde la UI.
- **Piezas capturadas:** el robot las deposita en una bandeja lateral.
- **Contexto:** exposición pública → prioridad en robustez, recuperación de errores y seguridad.

## 2. Arquitectura de hardware

```
┌─────────────────────────────────────────────────────┐
│ Raspberry Pi 5 (Raspberry Pi OS 64-bit)             │
│  ├── Backend Python (FastAPI + python-chess)        │
│  ├── Stockfish (motor UCI, nivel ajustable)         │
│  ├── ur_rtde → UR10e (Ethernet)                     │
│  ├── Lectura matriz sensores (GPIO / I2C)           │
│  └── Frontend React (kiosk en pantalla HDMI)        │
└─────────────────────────────────────────────────────┘
         │ Ethernet                │ GPIO/I2C
   ┌─────▼─────┐            ┌──────▼──────┐
   │ UR10e     │            │ Tablero 8x8 │
   │ + Robotiq │            │ reed/hall   │
   └───────────┘            └─────────────┘
```

### 2.1 Robot
- **UR10e** con controlador e-Series (PolyScope 5.x).
- Comunicación desde la Pi por **RTDE** usando la librería `ur_rtde` (SDU). Alternativa/fallback: envío de URScript por socket puerto 30002.
- Red: Pi y UR en la misma subred Ethernet (switch dedicado).

### 2.2 Garra
- **Robotiq** (modelo a definir; recomendado **Hand-E** por precisión y piezas pequeñas, alternativa 2F-85).
- Control preferido: **URCap Robotiq instalado en el UR** → la Pi comanda apertura/cierre vía registros del UR (RTDE input registers o URScript `rq_move()`).
- Alternativa: Modbus RTU directo al conector de herramienta.
- Parámetros por tipo de pieza: apertura, fuerza (baja, piezas livianas), velocidad.

### 2.3 Tablero sensorizado
- Matriz 8×8 de sensores **reed o Hall** (solo presencia, sin identidad de pieza). Cada pieza lleva imán en la base.
- Lectura por barrido de filas/columnas.
- Frecuencia de escaneo: ≥ 20 Hz con debounce por software (2–3 lecturas estables).
- Acceso GPIO en Pi 5 mediante `gpiod` (libgpiod v2) — no usar RPi.GPIO (incompatible con Pi 5).

### 2.4 Pantalla
- Monitor HDMI en modo kiosk (Chromium fullscreen) mostrando la UI React.

### 2.5 Elementos físicos adicionales
- **Bandeja lateral de capturas** con posiciones en grilla (el robot apila ordenadamente).
- **Bandeja de reserva** para piezas de promoción (mínimo 2 damas extra).
- **Botón físico de confirmación de jugada** del humano (recomendado; simplifica enormemente la detección de fin de jugada). Alternativa: timeout de estabilidad del tablero.
- Botón de parada de emergencia accesible (además del e-stop del UR).

## 3. Arquitectura de software

### 3.1 Stack
| Componente | Tecnología |
|---|---|
| Backend | Python 3.11+, FastAPI, WebSocket |
| Reglas de ajedrez | `python-chess` |
| Motor | Stockfish (binario ARM64) vía UCI |
| Robot | `ur_rtde` |
| GPIO | `gpiod` / `smbus2` (si MCP23017) |
| Frontend | React + Vite, `react-chessboard`, WebSocket client |
| Servicio | systemd (arranque automático, watchdog) |

### 3.2 Módulos del backend
1. **`board_sensor`** — barrido de la matriz, debounce, publica bitmap 64 bits de ocupación.
2. **`game_state`** — núcleo del sistema. Mantiene la partida en `python-chess`. Como los sensores solo detectan presencia, **la identidad de cada pieza se infiere por seguimiento de estado**: partiendo de la posición inicial conocida, cada jugada legal actualiza el mapa casilla→pieza.
3. **`move_detector`** — máquina de estados que interpreta los cambios del bitmap durante el turno humano:
   - Jugada simple: casilla origen se vacía → casilla destino se ocupa.
   - Captura: pieza rival levantada + pieza propia colocada en esa casilla (secuencia con estados intermedios).
   - Enroque: 4 eventos (rey y torre).
   - En passant: destino ocupado + peón capturado retirado de casilla distinta.
   - Valida contra jugadas legales de `python-chess`; si el cambio no corresponde a ninguna jugada legal → estado de error y aviso en UI.
   - Fin de jugada: botón de confirmación (o estabilidad ≥ N segundos).
4. **`engine`** — wrapper UCI de Stockfish. Dificultad ajustable por `UCI_LimitStrength` + `UCI_Elo` (rango ~1320–3000) o `Skill Level` 0–20, más límite de tiempo por jugada. Expone también la **evaluación continua** de la posición para la UI.
5. **`robot_controller`** — traducción de jugada UCI (ej. `e2e4`) a secuencia de movimientos:
   - Trayectoria: aproximación sobre la casilla a altura segura → descenso → cierre garra → ascenso a **altura de tránsito** (por encima de la pieza más alta, el rey) → traslado → descenso → apertura → retirada.
   - Capturas: primero retirar pieza capturada a la bandeja (siguiente posición libre de la grilla), luego mover la pieza propia.
   - Enroque: dos secuencias pick&place.
   - Promoción: depositar peón en bandeja de capturas, tomar dama de la bandeja de reserva.
   - `moveL` con blending para suavidad; velocidad y aceleración reducidas (entorno público).
6. **`calibration`** — rutina asistida:
   - Teach de 4 esquinas del tablero (freedrive o jog desde UI) → transformación para calcular las 64 posiciones.
   - Teach de bandeja de capturas y bandeja de reserva.
   - Tabla de alturas y aperturas de garra por tipo de pieza (peón, torre, caballo, alfil, dama, rey).
   - Persistencia en JSON/YAML.
7. **`api`** — REST + WebSocket para la UI: estado de partida, evaluación, historial de jugadas, control de dificultad, nueva partida, modo calibración, estado del robot y de sensores.

### 3.3 Frontend (UI de exposición)
- Tablero virtual sincronizado en tiempo real.
- **Barra de evaluación** (centipawns/mate) actualizada durante el análisis.
- Historial de jugadas en notación SAN.
- Selector de dificultad (ej. Principiante / Intermedio / Avanzado / Máximo).
- Indicador de turno y mensajes al público ("Pensando…", "Tu turno", "¡Jaque!").
- Panel de operador (oculto/protegido): calibración, resync, reinicio, diagnóstico de sensores (mapa de ocupación en vivo).

## 4. Seguridad (crítico en exposición)

- Configurar en el UR10e **planos de seguridad** que limiten el volumen de trabajo al tablero + bandejas.
- Velocidad y fuerza reducidas (modo colaborativo, límites TCP configurados en Safety Configuration del UR).
- El robot **solo se mueve en su turno** y tras confirmación de jugada; nunca mientras el humano tiene la mano sobre el tablero (opcional: cortina/sensor de zona o regla operativa con botón).
- Manejo de protective stop: detección vía RTDE, pausa de partida, mensaje en UI, reanudación asistida.
- Delimitación física del área de alcance del robot para el público.

## 5. Manejo de errores y recuperación

- **Jugada humana ilegal:** UI indica el error y pide restaurar; el sistema muestra qué casillas no coinciden.
- **Desincronización tablero/estado:** modo *resync* — la UI muestra la posición esperada y el mapa real de sensores; el operador corrige piezas hasta que coincidan.
- **Fallo de agarre** (pieza no tomada — verificable por sensor del tablero tras el pick): reintento automático (máx. 2) y luego aviso al operador.
- **Pérdida de conexión con el UR:** reconexión automática RTDE con backoff; partida pausada.
- Logging completo (jugadas, eventos de sensores, comandos al robot) para diagnóstico.

## 6. Plan de desarrollo por fases (para Claude Code)

### Fase 1 — Núcleo de juego (sin hardware)
- Proyecto Python: `game_state` + `engine` + simulador de tablero por consola/API.
- Tests: detección de jugadas a partir de diffs de bitmap (incluyendo capturas, enroque, en passant, promoción).

### Fase 2 — Tablero sensorizado
- Driver de matriz (gpiod/MCP23017), debounce, publicación por WebSocket.
- Herramienta de diagnóstico visual de sensores.
- Integrar `move_detector` con hardware real.

### Fase 3 — Robot y garra
- Conexión `ur_rtde`, jog seguro, rutina de calibración de esquinas y bandejas.
- Pick & place de una pieza; luego tabla completa de piezas.
- Integración garra Robotiq (URCap/registros).

### Fase 4 — Integración completa
- Partida completa end-to-end con manejo de capturas y jugadas especiales.
- Recuperación de errores y resync.

### Fase 5 — UI de exposición y puesta a punto
- Frontend React kiosk con evaluación y dificultad.
- systemd, arranque automático, pruebas de estrés (partidas continuas), ajuste de velocidades y ergonomía para el público.

## 7. Decisiones pendientes

- [ ] Modelo definitivo de garra Robotiq (recomendado Hand-E o 2F-85) y método de control (URCap vs Modbus).
- [ ] Electrónica de lectura de matriz: barrido con diodos vs 4× MCP23017 (recomendado MCP23017 por simplicidad de cableado).
- [ ] Botón físico de confirmación de jugada vs timeout de estabilidad (recomendado botón).
- [ ] Dimensiones del tablero y de las piezas (define aperturas de garra y alturas).
- [ ] Reloj de partida / límite de tiempo para el humano (opcional para la expo).

## 8. Estructura de repositorio propuesta

```
chess-robot/
├── backend/
│   ├── app/
│   │   ├── board_sensor/
│   │   ├── game_state/
│   │   ├── move_detector/
│   │   ├── engine/
│   │   ├── robot_controller/
│   │   ├── calibration/
│   │   └── api/
│   ├── tests/
│   └── config/          # calibración, parámetros de piezas, red
├── frontend/            # React + Vite
├── firmware-docs/       # esquemas de la matriz de sensores
├── deploy/              # systemd units, kiosk setup
└── docs/
```
