"""Bucle de barrido del tablero: lee el driver a frecuencia fija, aplica
debounce y notifica a los suscriptores cada bitmap estable nuevo.

Corre en un hilo propio; los callbacks se invocan desde ese hilo (los
consumidores asíncronos deben puentear con ``call_soon_threadsafe`` o sondear
``latest``).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app.board_sensor.bitmap import Bitmap
from app.board_sensor.debounce import Debouncer
from app.board_sensor.driver import SensorDriver

logger = logging.getLogger(__name__)

BitmapCallback = Callable[[Bitmap], None]


class BoardScanner:
    def __init__(
        self,
        driver: SensorDriver,
        scan_hz: float = 30.0,
        stable_reads: int = 3,
    ) -> None:
        self._driver = driver
        self._interval = 1.0 / scan_hz
        self._debouncer = Debouncer(stable_reads=stable_reads)
        self._subscribers: list[BitmapCallback] = []
        self._lock = threading.Lock()
        self._latest: Bitmap | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.read_errors = 0

    # ------------------------------------------------------------- ciclo de vida

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="board-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ---------------------------------------------------------------- consumo

    @property
    def latest(self) -> Bitmap | None:
        """Último bitmap estable (None hasta la primera estabilización)."""
        with self._lock:
            return self._latest

    def set_stable_reads(self, stable_reads: int) -> None:
        """Cambia el debounce en caliente (ajuste desde la UI)."""
        self._debouncer.set_stable_reads(stable_reads)

    def subscribe(self, callback: BitmapCallback) -> None:
        with self._lock:
            self._subscribers.append(callback)

    def unsubscribe(self, callback: BitmapCallback) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def wait_for_bitmap(self, bitmap: Bitmap, timeout: float) -> bool:
        """Bloquea hasta que el bitmap estable sea el esperado (útil en tests y
        para verificar el resultado de un movimiento del robot en Fase 3)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.latest == bitmap:
                return True
            time.sleep(self._interval / 2)
        return self.latest == bitmap

    # ----------------------------------------------------------------- interno

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                raw = self._driver.read()
            except Exception:
                self.read_errors += 1
                logger.exception("Error leyendo la matriz de sensores")
            else:
                stable = self._debouncer.feed(raw)
                if stable is not None:
                    self._publish(stable)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, self._interval - elapsed))

    def _publish(self, bitmap: Bitmap) -> None:
        with self._lock:
            self._latest = bitmap
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(bitmap)
            except Exception:
                logger.exception("Error en suscriptor del scanner")
