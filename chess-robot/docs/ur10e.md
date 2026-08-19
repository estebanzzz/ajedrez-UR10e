# UR10e — control por RTDE y calibración

Robot de la celda: **UR10e**, PolyScope 5.25, IP `192.168.0.25`.
Garra: **Robotiq Hand-E** con el URCap instalado (servidor socket en el
puerto 63352 del robot; no requiere programa corriendo en el pendant).

## Requisitos en el pendant (una sola vez)

1. **Control Remoto**: Ajustes → Sistema → Control Remoto. Solo puede
   activarse desde el modo **Automático** (no desde Manual): cambiar el modo
   con el ícono de arriba a la derecha y luego elegir *Remoto*.
2. **Fieldbus deshabilitado**: Instalación → Bus de campo → EtherNet/IP y
   PROFINET en *Deshabilitado*, sin unidades MODBUS. Si quedan activos,
   ocupan los registros RTDE y `ur_rtde` no puede conectarse
   (`RTDE input registers are already in use`).

## Arranque del backend con el robot

```bash
CHESS_DRIVER=mock CHESS_ROBOT_HOST=192.168.0.25 python -m app.api.server
```

En la Pi se usa el driver real del tablero (`CHESS_DRIVER=s7`). La dependencia
`ur_rtde` está en `requirements-pi.txt`.

## Calibración (teach por freedrive)

Abrir `http://<backend>:8000/calibration`:

1. **Activar freedrive** y llevar la punta de la garra (cerrada) al punto que
   indica el asistente: centros de las casillas a1, h1, a8 y h8 tocando la
   superficie, y luego los slots de referencia de cada bandeja (slot 0, slot
   vecino de la misma fila y, si tiene más de una fila, el primer slot de la
   fila 2).
2. **Capturar punto** en cada posición. *Deshacer último* permite repetir.
3. **Guardar calibración**: valida medidas (tamaño de casilla, lados opuestos,
   alturas) y escribe `backend/config/calibration.json`. El servidor debe
   reiniciarse para que la partida use la nueva geometría.
4. **Prueba de posicionamiento**: mueve el TCP lento hasta una altura segura
   sobre la casilla elegida, para verificar la calibración sin tocar piezas.

Las 64 casillas se interpolan bilinealmente entre las 4 esquinas
(`BoardGeometry`), lo que absorbe una leve inclinación o rotación del tablero.

## Garra Hand-E

`RobotiqGripper` habla con el URCap por el puerto 63352 (`SET/GET POS SPE
FOR...`). Mientras la garra no esté montada físicamente el URCap responde
`STA ?`: el backend lo detecta, marca `gripper.connected = false` en
`/api/robot/status` y los `gripper_move` quedan en no-op con warning — el
resto (movimientos, calibración) funciona igual. Al montarla, la primera
orden de movimiento la activa automáticamente (hace un ciclo de referencia
de cierre/apertura: hacerlo sin pieza en la garra).

## Estabilidad: caída de la sesión RTDE

Si el robot cierra la conexión RTDE (salir del modo Remoto, apagado, corte de
red), el hilo interno de reconexión de `ur_rtde` puede tumbar el proceso
entero con un segfault (observado en Windows con ur_rtde 1.6.5: `End of
file → Reconnecting... → exit 139`). El proceso no puede defenderse de un
segfault en la librería nativa, así que el backend debe correr **supervisado**:

- Desarrollo: relanzarlo en un bucle (`while true; do uvicorn ...; sleep 3; done`).
- Producción (Pi): unidad systemd con `Restart=always` (ver `deploy/kiosk.md`).

## API del robot

| Endpoint | Uso |
| --- | --- |
| `GET /api/robot/status` | conexión, pose TCP, articulaciones, modo, garra |
| `POST /api/robot/freedrive` | `{"enabled": true\|false}` |
| `POST /api/robot/gripper` | `{"opening_mm": 0-50, "force": 0-1}` |
| `POST /api/calibration/start\|capture\|back\|save` | asistente de teach |
| `GET /api/calibration/state` | estado de la sesión |
| `POST /api/calibration/goto` | `{"square": "e4", "clearance_mm": 80}` prueba lenta |
