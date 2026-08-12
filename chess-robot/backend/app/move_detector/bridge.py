"""Integración del MoveDetector con el flujo real de sensores.

Suscribe el detector al ``BoardScanner``: cada bitmap estable alimenta la
máquina de estados del turno humano. El mismo puente sirve con el driver real
(Fase 2) y con el ``MockDriver`` en desarrollo.
"""

from __future__ import annotations

import threading
from typing import Callable

import chess

from app.board_sensor.bitmap import Bitmap
from app.board_sensor.scanner import BoardScanner
from app.move_detector.detector import (
    DetectionError,
    DetectionResult,
    DetectorPhase,
    MoveDetector,
)

PhaseCallback = Callable[[DetectorPhase], None]


class SensorDetectorBridge:
    """Conecta el scanner con el detector durante el turno humano."""

    def __init__(
        self,
        scanner: BoardScanner,
        board: chess.Board,
        on_phase_change: PhaseCallback | None = None,
    ) -> None:
        self._scanner = scanner
        self._detector = MoveDetector(board)
        self._on_phase_change = on_phase_change
        self._lock = threading.Lock()
        self._phase = self._detector.phase
        self._active = False

    @property
    def phase(self) -> DetectorPhase:
        with self._lock:
            return self._phase

    def start_turn(self) -> None:
        """Comienza a escuchar sensores para el turno del humano."""
        with self._lock:
            self._detector.begin_turn()
            self._phase = self._detector.phase
            if not self._active:
                self._scanner.subscribe(self._on_bitmap)
                self._active = True

    def stop(self) -> None:
        with self._lock:
            if self._active:
                self._scanner.unsubscribe(self._on_bitmap)
                self._active = False

    def confirm(self) -> DetectionResult | DetectionError:
        """Botón de confirmación pulsado: resolver la jugada y dejar de escuchar."""
        with self._lock:
            result = self._detector.confirm()
        self.stop()
        return result

    def _on_bitmap(self, bitmap: Bitmap) -> None:
        with self._lock:
            new_phase = self._detector.update(bitmap)
            changed = new_phase != self._phase
            self._phase = new_phase
        if changed and self._on_phase_change is not None:
            self._on_phase_change(new_phase)
