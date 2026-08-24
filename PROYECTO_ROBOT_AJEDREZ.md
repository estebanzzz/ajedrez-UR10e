# Proyecto: Robot Ajedrecista UR10e — Exposición

## 1. Resumen

Sistema demostrativo para exposición en el que un robot colaborativo **Universal Robots UR10e** juega al ajedrez contra un humano sobre un tablero físico. La detección de piezas es por **visión artificial (cámara cenital + OpenCV)**. El control central es una **Raspberry Pi 5**, que ejecuta el motor de ajedrez, procesa la imagen del tablero, comanda el robot y la garra **Robotiq**, y sirve una interfaz gráfica en pantalla para el público con tablero virtual y evaluación de la partida en tiempo real.

- **Modo de juego:** contra humano, con dificultad ajustable desde la UI.
- **Piezas capturadas:** el robot las deposita en una bandeja lateral.
- **Contexto:** exposición pública → prioridad en robustez, recuperación de errores y seguridad.

## 2. Arquitectura de hardware

```
┌─────────────────────────────────────────────────────┐
│ Raspberry Pi 5 (Raspberry Pi OS 64-bit)             │
│  ├── Backend Python (FastAPI + python-chess)        │
│  ├── OpenCV (detección de piezas por cámara)        │
│  ├── Stockfish (motor UCI, nivel ajustable)         │
│  ├── ur_rtde → UR10e (Ethernet)                     │
│  ├── python-snap7 → S7-1200 (Ethernet, protocolo S7)│
│  └── Frontend React (kiosk en pantalla HDMI)        │
└─────────────────────────────────────────────────────┘
    │ USB/CSI       │ Ethernet         │ Ethernet (S7)
┌───▼──────────┐ ┌──▼────────┐  ┌──────▼──────────────────┐
│ Cámara       │ │ UR10e     │  │ PLC S7-1200 (CPU 1215C) │
│ cenital      │ │ + Robotiq │  │  panel de operación:    │
│ sobre el     │ └───────────┘  │  ├── botón confirmación │
│ tablero 8x8  │                │  ├── baliza/semáforo    │
└──────────────┘                │  └── e-stop auxiliar    │
                                └─────────────────────────┘
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

### 2.3 Detección de piezas por visión artificial (OpenCV)
- **Cámara industrial Basler** cenital fija sobre el tablero, adquisición vía **pylon/pypylon** (SDK oficial de Basler). Montada de forma que vea las 64 casillas sin que el robot la obstruya en reposo.
- Configuración de cámara recomendada (desde pylon Viewer): **exposición y balance de blancos manuales** (los automáticos cambian la imagen cuando el brazo entra en cuadro y desestabilizan la clasificación), guardados en un **UserSet como startup set** para que la cámara arranque siempre igual.
- **Sin identidad ni bando por imagen**: la visión clasifica cada casilla en solo **2 clases — vacía / ficha**. Ambos bandos usan fichas **oscuras** sobre el tablero claro (p. ej. azul y gris) y se detectan por el mismo camino de píxeles oscuros, robusto ante la luz. La identidad y el bando (peón, torre…, blanca/negra) se siguen infiriendo por seguimiento de estado en `game_state`, igual que antes.
- **Pipeline** (clásico, sin deep learning):
  1. **Calibración de perspectiva**: el operador marca las 4 esquinas del área de juego con clicks → homografía → imagen cenital rectificada, dividida en 64 celdas.
  2. **Clasificación por casilla**: cada píxel se compara contra el **fondo de su propia celda** (brillo de las 4 esquinas, siempre visibles); una casilla está ocupada si su disco central queda mayormente por debajo del umbral píxel-oscuro **aprendido** en el entrenamiento — señal relativa, inmune al nivel de luz.
  3. **Oclusión**: si el frame difiere bruscamente del anterior (mano o brazo sobre el tablero), se mantiene el último bitmap estable en lugar de publicar lecturas falsas.
  4. **Debounce** por software (N lecturas estables), reutilizando el `BoardScanner` existente.
  5. **Resolución de jugada**: el detector continuo estándar (el mismo de los drivers de presencia) sigue los cambios del bitmap durante el turno humano y resuelve la jugada al confirmar; las casillas tocadas transitoriamente desambiguan capturas. La jugada del robot se verifica con el brazo ya retirado a su **posición de espera** fuera del tablero (sin sombra ni oclusión).
- **Entrenamiento auto-etiquetado** (diseñado para ser trivial para el operador): se capturan **una tanda de fotos del tablero vacío y una de la posición inicial**. Las etiquetas se conocen solas: 64 casillas vacías en la primera; filas 1–2 y 7–8 con ficha y 3–6 vacías en la segunda. Nada que etiquetar a mano. Re-entrenar tras cambiar iluminación o tablero toma ~1 minuto.
- El resultado es el **mismo bitmap de ocupación de 64 bits** que producía la matriz de sensores, por lo que `move_detector`, `game_state` y el resto del sistema no cambian.
- Legado: la matriz 8×8 de reed vía PLC (`s7`) y vía GPIO (`matrix`) queda implementada como respaldo, pero **deja de ser la solución de producción**.

### 2.3.1 PLC S7-1200 (panel de operación)
- El PLC S7-1200 (CPU 1215C) se conserva en el sistema como **panel de operación**: botón físico de confirmación de jugada, baliza/semáforo y contacto auxiliar del e-stop. Ya no barre la matriz de sensores.
- La Pi sigue comunicándose por Ethernet con **python-snap7**. Pendiente: adaptar `PanelLink` para funcionar junto al driver de visión (hoy asume que el driver del tablero es el S7).

### 2.4 Pantalla
- Monitor HDMI en modo kiosk (Chromium fullscreen) mostrando la UI React.

### 2.5 Elementos físicos adicionales
- **Bandeja lateral de capturas** con posiciones en grilla (el robot apila ordenadamente).
- **Bandeja de reserva** para piezas de promoción (mínimo 2 damas extra).
- **Botón físico de confirmación de jugada** del humano, cableado a una DI del PLC (simplifica enormemente la detección de fin de jugada). Alternativa: timeout de estabilidad del tablero.
- **Baliza/semáforo** comandada por el PLC según el estado de la partida (verde: turno humano; rojo: robot en movimiento; amarillo: error/resync).
- Botón de parada de emergencia accesible (además del e-stop del UR), con contacto auxiliar leído por el PLC.

## 3. Arquitectura de software

### 3.1 Stack
| Componente | Tecnología |
|---|---|
| Backend | Python 3.11+, FastAPI, WebSocket |
| Reglas de ajedrez | `python-chess` |
| Motor | Stockfish (binario ARM64) vía UCI |
| Visión (detección de piezas) | OpenCV (`opencv-python`) + numpy; cámara Basler vía `pypylon` |
| Robot | `ur_rtde` |
| PLC (panel: botón/baliza) | `python-snap7` → S7-1215C (DB por protocolo S7) |
| Tablero legado (respaldo) | matriz reed vía PLC (`s7`) o GPIO (`gpiod`) |
| Frontend | React + Vite, `react-chessboard`, WebSocket client |
| Servicio | systemd (arranque automático, watchdog) |

### 3.2 Módulos del backend
1. **`vision`** — detección de piezas por cámara: captura, homografía de perspectiva, clasificación de las 64 casillas (vacía/blanca/negra), manejo de oclusión y entrenamiento auto-etiquetado. Expone un `VisionDriver` que implementa la misma interfaz `SensorDriver` que los drivers legados, publicando el bitmap de 64 bits de ocupación.
2. **`board_sensor`** — infraestructura común de lectura del tablero: bitmap, debounce, `BoardScanner` (hilo de barrido y suscriptores) y los drivers legados de matriz reed (`s7`, `matrix_gpio`, `mock`).
3. **`game_state`** — núcleo del sistema. Mantiene la partida en `python-chess`. Como la detección solo aporta presencia (y color), **la identidad de cada pieza se infiere por seguimiento de estado**: partiendo de la posición inicial conocida, cada jugada legal actualiza el mapa casilla→pieza.
4. **`move_detector`** — máquina de estados que interpreta los cambios del bitmap durante el turno humano:
   - Jugada simple: casilla origen se vacía → casilla destino se ocupa.
   - Captura: pieza rival levantada + pieza propia colocada en esa casilla (secuencia con estados intermedios).
   - Enroque: 4 eventos (rey y torre).
   - En passant: destino ocupado + peón capturado retirado de casilla distinta.
   - Valida contra jugadas legales de `python-chess`; si el cambio no corresponde a ninguna jugada legal → estado de error y aviso en UI.
   - Fin de jugada: botón de confirmación (o estabilidad ≥ N segundos).
5. **`engine`** — wrapper UCI de Stockfish. Dificultad ajustable por `UCI_LimitStrength` + `UCI_Elo` (rango ~1320–3000) o `Skill Level` 0–20, más límite de tiempo por jugada. Expone también la **evaluación continua** de la posición para la UI.
6. **`robot_controller`** — traducción de jugada UCI (ej. `e2e4`) a secuencia de movimientos:
   - Trayectoria: aproximación sobre la casilla a altura segura → descenso → cierre garra → ascenso a **altura de tránsito** (por encima de la pieza más alta, el rey) → traslado → descenso → apertura → retirada.
   - Capturas: primero retirar pieza capturada a la bandeja (siguiente posición libre de la grilla), luego mover la pieza propia.
   - Enroque: dos secuencias pick&place.
   - Promoción: depositar peón en bandeja de capturas, tomar dama de la bandeja de reserva.
   - `moveL` con blending para suavidad; velocidad y aceleración reducidas (entorno público).
7. **`calibration`** — rutina asistida:
   - Teach de 4 esquinas del tablero (freedrive o jog desde UI) → transformación para calcular las 64 posiciones.
   - Teach de bandeja de capturas y bandeja de reserva.
   - Tabla de alturas y aperturas de garra por tipo de pieza (peón, torre, caballo, alfil, dama, rey).
   - Calibración de la cámara: marcado de las 4 esquinas en imagen y entrenamiento del clasificador (ver módulo `vision`).
   - Persistencia en JSON/YAML.
8. **`api`** — REST + WebSocket para la UI: estado de partida, evaluación, historial de jugadas, control de dificultad, nueva partida, modo calibración, estado del robot y de sensores.

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
- **Desincronización tablero/estado:** modo *resync* — la UI muestra la posición esperada y el mapa real detectado por la cámara; el operador corrige piezas hasta que coincidan.
- **Fallo de agarre** (pieza no tomada — verificable por la cámara tras el pick): reintento automático (máx. 2) y luego aviso al operador.
- **Cambio de iluminación** (la clasificación pierde confianza): aviso en el panel de operador y re-entrenamiento rápido (~1 min, tablero vacío + posición inicial).
- **Pérdida de conexión con el UR:** reconexión automática RTDE con backoff; partida pausada.
- Logging completo (jugadas, eventos de sensores, comandos al robot) para diagnóstico.

## 6. Plan de desarrollo por fases (para Claude Code)

### Fase 1 — Núcleo de juego (sin hardware)
- Proyecto Python: `game_state` + `engine` + simulador de tablero por consola/API.
- Tests: detección de jugadas a partir de diffs de bitmap (incluyendo capturas, enroque, en passant, promoción).

### Fase 2 — Detección del tablero (visión artificial)
- Módulo `vision`: cámara, homografía de 4 esquinas, clasificador de casillas (vacía/blanca/negra), `VisionDriver` con la interfaz `SensorDriver`, debounce y publicación por WebSocket.
- Herramientas de operador: calibración por clicks y entrenamiento auto-etiquetado (tablero vacío + posición inicial), con vista previa en vivo.
- Herramienta de diagnóstico visual de ocupación (ya existente, común a todos los drivers).
- Integrar `move_detector` con la cámara real.
- (Histórico: esta fase se implementó primero con matriz de reed vía PLC/GPIO; esos drivers quedan como respaldo.)

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
- [x] Detección de piezas: **visión artificial con cámara cenital + OpenCV** (clasificación vacía/blanca/negra por casilla, entrenamiento auto-etiquetado). Reemplaza a la matriz de reed vía PLC/GPIO, que queda como respaldo legado.
- [x] Cámara: **Basler** (ya disponible, conectada con pylon; adquisición por pypylon). Pendiente solo el soporte/altura de montaje cenital definitivo.
- [ ] Adaptar `PanelLink` (botón + baliza vía S7) para convivir con el driver de visión.
- [ ] Botón físico de confirmación de jugada vs timeout de estabilidad (recomendado botón).
- [ ] Dimensiones del tablero y de las piezas (define aperturas de garra y alturas).
- [ ] Reloj de partida / límite de tiempo para el humano (opcional para la expo).

## 8. Estructura de repositorio propuesta

```
chess-robot/
├── backend/
│   ├── app/
│   │   ├── vision/          # cámara, homografía, clasificador, VisionDriver
│   │   ├── board_sensor/    # bitmap, debounce, scanner + drivers legados
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
