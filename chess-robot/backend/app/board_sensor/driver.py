"""Interfaz común de drivers del tablero sensorizado.

Un driver lee la matriz 8x8 y devuelve el bitmap crudo de ocupación (sin
debounce; de eso se encarga ``Debouncer``/``BoardScanner``).
"""

from __future__ import annotations

import threading
from typing import Protocol

from app.board_sensor.bitmap import Bitmap


class SensorDriver(Protocol):
    """Protocolo que implementan todos los drivers (real, mock)."""

    def read(self) -> Bitmap:
        """Lectura instantánea del bitmap de ocupación (64 bits)."""
        ...

    def close(self) -> None:
        ...


class MockDriver:
    """Driver simulado para desarrollo y tests: el bitmap se fija por software.

    Thread-safe: el scanner lee desde su hilo mientras los tests o la UI de
    diagnóstico modifican el estado.
    """

    def __init__(self, initial: Bitmap = 0) -> None:
        self._lock = threading.Lock()
        self._bitmap = initial

    def read(self) -> Bitmap:
        with self._lock:
            return self._bitmap

    def set_bitmap(self, bitmap: Bitmap) -> None:
        with self._lock:
            self._bitmap = bitmap

    def set_square(self, square: int, occupied: bool) -> None:
        with self._lock:
            if occupied:
                self._bitmap |= 1 << square
            else:
                self._bitmap &= ~(1 << square)

    def toggle_square(self, square: int) -> None:
        with self._lock:
            self._bitmap ^= 1 << square

    def close(self) -> None:
        pass
