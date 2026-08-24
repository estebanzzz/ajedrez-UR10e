"""Disparadores por tiempo: lo que el robot dice *mientras* el humano piensa.

El orquestador avisa los eventos de la partida (jugadas, jaques, fin); este
hilo cubre lo que depende del reloj y de la cámara, sondeando ``status()``:

- tardanza del humano (20 / 45 / 90 s desde que empezó su turno),
- pieza levantada y devuelta sin mover (``detector_phase``),
- relleno aleatorio durante el turno humano,
- modo atractor cuando no hay partida, y comentarios durante la demo.

``tick()`` es público y determinista (reloj inyectable) para poder probarlo
sin hilos.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

from app.personality.commentator import Commentator, Event

logger = logging.getLogger(__name__)

SLOW_STEPS: tuple[tuple[float, Event], ...] = (
    (20.0, Event.HUMAN_SLOW_20),
    (45.0, Event.HUMAN_SLOW_45),
    (90.0, Event.HUMAN_SLOW_90),
)
ATTRACT_EVERY_S = 90.0
SELF_PLAY_EVERY_S = 45.0
FILLER_MIN_GAP_S = 25.0
FILLER_AFTER_S = 8.0
FILLER_MEAN_PERIOD_S = 30.0  # en promedio, un relleno cada tanto
BOOT_DELAY_S = 3.0


class Heckler:
    def __init__(
        self,
        commentator: Commentator,
        get_status: Callable[[], dict],
        *,
        interval: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        boot_delay: float | None = BOOT_DELAY_S,
    ) -> None:
        """``boot_delay=None`` omite el saludo de arranque."""
        self._commentator = commentator
        self._get_status = get_status
        self._interval = interval
        self._clock = clock
        self._rng = rng or random.Random()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        now = clock()
        self._boot_at: float | None = None if boot_delay is None else now + boot_delay
        self._phase: str | None = None
        self._phase_since = now
        self._detector: str | None = None
        self._slow_fired: set[Event] = set()
        self._last_filler = now
        self._last_attract = now
        self._last_self_play = now

    # ---------------------------------------------------------------- hilo

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="heckler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:
                logger.exception("Fallo en el heckler")

    # --------------------------------------------------------------- lógica

    def tick(self) -> None:
        now = self._clock()
        if self._boot_at is not None and now >= self._boot_at:
            self._boot_at = None
            self._commentator.say(Event.BOOT)

        status = self._get_status()
        phase = status.get("phase")
        detector = status.get("detector_phase")
        self_play = status.get("mode") == "self_play"

        if phase != self._phase:
            self._phase = phase
            self._phase_since = now
            self._slow_fired.clear()
            self._last_filler = now
            self._detector = detector
            if phase == "idle":
                self._last_attract = now
            if phase == "robot_turn" and self_play:
                self._last_self_play = now

        if phase == "human_turn":
            self._human_turn(now, detector)
        elif phase == "idle":
            if now - self._last_attract >= ATTRACT_EVERY_S:
                self._last_attract = now
                self._commentator.say(Event.ATTRACT)
        elif self_play and phase == "robot_turn":
            if now - self._last_self_play >= SELF_PLAY_EVERY_S:
                self._last_self_play = now
                self._commentator.say(Event.SELF_PLAY, self_play=True)
        self._detector = detector

    def _human_turn(self, now: float, detector: str | None) -> None:
        elapsed = now - self._phase_since
        # Pieza en la mano / devuelta: reacción inmediata, una por transición.
        if detector != self._detector:
            if detector == "in_progress":
                self._commentator.say(Event.PIECE_LIFTED)
                return
            if detector == "idle" and self._detector == "in_progress":
                self._commentator.say(Event.PIECE_RETURNED)
                return
        # Tardanza: cada escalón una sola vez por turno.
        for threshold, event in SLOW_STEPS:
            if elapsed >= threshold and event not in self._slow_fired:
                self._slow_fired.add(event)
                self._commentator.say(event)
                return
        # Relleno ocasional (proceso de Poisson discreto por tick).
        if (
            elapsed >= FILLER_AFTER_S
            and now - self._last_filler >= FILLER_MIN_GAP_S
            and self._rng.random() < self._interval / FILLER_MEAN_PERIOD_S
        ):
            self._last_filler = now
            self._commentator.say(Event.FILLER)
