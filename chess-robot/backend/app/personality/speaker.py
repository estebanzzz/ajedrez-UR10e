"""Reproducción local de los audios (parlante conectado a la Pi).

Se usa cuando ``CHESS_ROBOT_SPEECH_PLAYER`` apunta a un reproductor de línea
de comandos (p. ej. ``mpg123 -q``); si no está definida, la UI del kiosk
reproduce los audios en el navegador y este módulo no interviene.

Un hilo consume una cola para no bloquear nunca al orquestador. Una frase de
prioridad alta (jaque, fin de partida) corta lo que esté sonando y vacía la
cola: es más importante que un relleno a medio decir.
"""

from __future__ import annotations

import logging
import queue
import shlex
import subprocess
import threading
from pathlib import Path

from app.personality.commentator import VOICE_DIR, Utterance

logger = logging.getLogger(__name__)

INTERRUPT_PRIORITY = 2


class LocalSpeaker:
    def __init__(self, player: str, voice_dir: Path = VOICE_DIR) -> None:
        self._cmd = shlex.split(player)
        if not self._cmd:
            raise ValueError("player vacío")
        self._voice_dir = voice_dir
        self._queue: queue.Queue[Utterance | None] = queue.Queue()
        self._lock = threading.Lock()
        self._current: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()

    def __call__(self, utterance: Utterance) -> None:
        """Listener para ``Commentator.subscribe``."""
        if utterance.priority >= INTERRUPT_PRIORITY:
            self._interrupt()
        self._queue.put(utterance)

    def close(self) -> None:
        self._interrupt()
        self._queue.put(None)
        self._thread.join(timeout=2.0)

    def _interrupt(self) -> None:
        with self._lock:
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            if self._current is not None and self._current.poll() is None:
                self._current.kill()

    def _run(self) -> None:
        while True:
            utterance = self._queue.get()
            if utterance is None:
                return
            for name in (utterance.sfx, utterance.audio):
                if name:
                    self._play(self._voice_dir / name)

    def _play(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Audio faltante: %s", path)
            return
        try:
            with self._lock:
                self._current = subprocess.Popen(
                    [*self._cmd, str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self._current.wait()
        except OSError:
            logger.exception("No se pudo reproducir %s con %s", path, self._cmd)
        finally:
            with self._lock:
                self._current = None
