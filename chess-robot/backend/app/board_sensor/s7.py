"""Driver del tablero vía PLC Siemens S7-1200 (CPU 1215C).

El PLC barre la matriz 8x8, lee el botón de confirmación y comanda la
baliza; publica todo en DBs que la Pi accede por Ethernet con python-snap7.
Dos DBs, cada lado escribe solo el suyo (no optimizados — ver
chess-robot/docs/plc-s7-1200.md):

DB de entradas (PLC → Pi, default DB1):
    DBB0..7   ocupación: byte n = fila n+1; bit m = columna (a=0 … h=7)
    DBB8      panel: bit 0 = botón confirmar, bit 1 = e-stop OK
    DBB9      reservado
    DBW10     heartbeat (el PLC lo incrementa en cada barrido completo)

DB de salidas (Pi → PLC, default DB2):
    DBB0      status → semáforo/baliza (códigos STATUS_*)

También se puede usar un único DB para ambos sentidos (``db_out`` igual a
``db_in``): en ese caso el status se escribe en el offset 12.

Cada ``read()`` trae los 12 bytes PLC→Pi en una sola transacción (~2-5 ms),
así el botón y el heartbeat viajan gratis con la ocupación.

El cliente snap7 se inyecta para poder testear sin PLC; ``open_s7`` crea el
real. Requiere en TIA: DB con acceso no optimizado y PUT/GET habilitado.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Protocol

from app.board_sensor.bitmap import Bitmap

logger = logging.getLogger(__name__)

READ_SIZE = 12  # DBB0..11 (ocupación + panel + heartbeat)
STATUS_OFFSET_SHARED_DB = 12  # un solo DB: el status va después de las entradas
STATUS_OFFSET_OWN_DB = 0  # DB de salidas propio: el status arranca en 0

# Códigos de status Pi→PLC (DBB12) — el PLC los traduce a la baliza.
STATUS_IDLE = 0
STATUS_HUMAN_TURN = 1  # verde
STATUS_ROBOT_MOVING = 2  # rojo
STATUS_ERROR = 3  # amarillo (error humano / resync)
STATUS_GAME_OVER = 4


class S7Client(Protocol):
    """Subconjunto de snap7.client.Client que usa el driver."""

    def db_read(self, db_number: int, start: int, size: int) -> bytearray: ...

    def db_write(self, db_number: int, start: int, data: bytearray) -> None: ...

    def disconnect(self) -> None: ...


class S7Driver:
    def __init__(
        self,
        client: S7Client,
        db_in: int = 1,
        db_out: int | None = 2,
        reconnect: Callable[[], None] | None = None,
    ) -> None:
        """``db_in``: DB que escribe el PLC (tablero/panel/heartbeat).
        ``db_out``: DB que escribe la Pi (baliza). Si es None o igual a
        ``db_in``, se usa el mismo DB con el status en el offset 12.
        ``reconnect``: rehace la conexión del cliente; ante un error de
        comunicación se reintenta la operación una vez (el PLC puede tirar
        la sesión, p. ej. al entrar TIA online — el driver debe recuperarse
        solo, es un sistema de exposición)."""
        self._client = client
        self._db_in = db_in
        self._db_out = db_out if db_out is not None else db_in
        self._status_offset = (
            STATUS_OFFSET_SHARED_DB if self._db_out == db_in else STATUS_OFFSET_OWN_DB
        )
        self._reconnect = reconnect
        self._lock = threading.Lock()
        # El cliente snap7 NO es thread-safe: el scanner (lecturas a 30 Hz) y
        # el PanelLink (escrituras de baliza) deben serializarse o el
        # protocolo se corrompe y el PLC resetea la sesión.
        self._io_lock = threading.Lock()
        self._button = False
        self._estop_ok = True
        self._heartbeat = -1
        self._last_heartbeat_change = time.monotonic()
        self._backoff_until = 0.0

    RECONNECT_BACKOFF_S = 2.0

    def _with_retry(self, operation: Callable[[], object]) -> object:
        with self._io_lock:
            try:
                return operation()
            except Exception:
                if self._reconnect is None:
                    raise
                # Sin backoff, el scanner (30 Hz) martilla al PLC con
                # conexiones nuevas y este empieza a resetear sesiones.
                if time.monotonic() < self._backoff_until:
                    raise
                logger.warning("Conexión S7 caída; reconectando…")
                try:
                    self._client.disconnect()
                except Exception:
                    pass
                try:
                    self._reconnect()
                except Exception:
                    self._backoff_until = time.monotonic() + self.RECONNECT_BACKOFF_S
                    raise
                return operation()

    # ------------------------------------------------- SensorDriver (scanner)

    def read(self) -> Bitmap:
        data = bytes(self._with_retry(lambda: self._client.db_read(self._db_in, 0, READ_SIZE)))
        heartbeat = int.from_bytes(data[10:12], "big")  # WORD Siemens: big-endian
        with self._lock:
            self._button = bool(data[8] & 0x01)
            self._estop_ok = bool(data[8] & 0x02)
            if heartbeat != self._heartbeat:
                self._heartbeat = heartbeat
                self._last_heartbeat_change = time.monotonic()
        return int.from_bytes(data[0:8], "little")

    def close(self) -> None:
        try:
            self._client.disconnect()
        except Exception:
            logger.exception("Error al desconectar del PLC")

    # --------------------------------------------------------- panel (cache)

    @property
    def button_pressed(self) -> bool:
        with self._lock:
            return self._button

    @property
    def estop_ok(self) -> bool:
        with self._lock:
            return self._estop_ok

    @property
    def heartbeat(self) -> int:
        with self._lock:
            return self._heartbeat

    def plc_alive(self, max_age_s: float = 2.0) -> bool:
        """El heartbeat cambió hace poco → el programa del PLC está corriendo."""
        with self._lock:
            return time.monotonic() - self._last_heartbeat_change < max_age_s

    # ------------------------------------------------------------- Pi → PLC

    def write_status(self, code: int) -> None:
        self._with_retry(
            lambda: self._client.db_write(
                self._db_out, self._status_offset, bytearray([code & 0xFF])
            )
        )


def open_s7(
    host: str,
    rack: int = 0,
    slot: int = 1,
    db_in: int = 1,
    db_out: int | None = 2,
) -> S7Driver:
    """Conecta al PLC real (import diferido: python-snap7 solo en la Pi).

    Si el PLC no responde en el arranque, el servidor igual levanta: la
    conexión se reintenta sola en cada lectura (con backoff).
    """
    import snap7

    client = snap7.client.Client()
    try:
        # Tipo de conexión 3 (S7 Basic): no compite con la sesión PG de TIA
        # Portal — con tipo PG (default), el PLC resetea una u otra al azar.
        client.set_connection_type(3)
    except Exception:
        logger.warning("set_connection_type no disponible en esta versión de snap7")

    def _connect() -> None:
        client.connect(host, rack, slot)

    try:
        _connect()
    except Exception as exc:
        logger.warning("PLC %s no disponible al iniciar (%s); se reintentará", host, exc)
    return S7Driver(client, db_in=db_in, db_out=db_out, reconnect=_connect)


# --------------------------------------------------------------------- panel


PHASE_STATUS_CODES = {
    "idle": STATUS_IDLE,
    "human_turn": STATUS_HUMAN_TURN,
    "human_error": STATUS_ERROR,
    "robot_turn": STATUS_ROBOT_MOVING,
    "resync": STATUS_ERROR,
    "game_over": STATUS_GAME_OVER,
}


class PanelLink:
    """Une el panel físico del PLC con la partida.

    - Flanco ascendente del botón de confirmación → ``on_button()``.
    - Cambio de fase de la partida → escribe el código de status (baliza).

    No hace lecturas propias al PLC: usa el estado cacheado por el scanner
    (que ya llama a ``S7Driver.read()`` a ~30 Hz).
    """

    def __init__(
        self,
        driver: S7Driver,
        get_phase: Callable[[], str],
        on_button: Callable[[], object],
        poll_s: float = 0.05,
    ) -> None:
        self._driver = driver
        self._get_phase = get_phase
        self._on_button = on_button
        self._poll_s = poll_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_button = False
        self._last_status: int | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="plc-panel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def poll_once(self) -> None:
        """Un ciclo de sondeo (expuesto para tests)."""
        button = self._driver.button_pressed
        if button and not self._last_button:
            try:
                self._on_button()
            except Exception:
                logger.exception("Error al procesar el botón de confirmación")
        self._last_button = button

        status = PHASE_STATUS_CODES.get(self._get_phase(), STATUS_IDLE)
        if status != self._last_status:
            try:
                self._driver.write_status(status)
                self._last_status = status
            except Exception:
                logger.exception("Error al escribir el status en el PLC")

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self._poll_s)
