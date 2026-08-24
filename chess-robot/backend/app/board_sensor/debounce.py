"""Debounce por software: un bitmap se considera estable tras N lecturas
consecutivas idénticas (spec: 2–3 lecturas a ≥20 Hz)."""

from __future__ import annotations

from app.board_sensor.bitmap import Bitmap


class Debouncer:
    def __init__(self, stable_reads: int = 3) -> None:
        if stable_reads < 1:
            raise ValueError("stable_reads debe ser >= 1")
        self._required = stable_reads
        self._stable: Bitmap | None = None
        self._candidate: Bitmap | None = None
        self._count = 0

    @property
    def stable(self) -> Bitmap | None:
        """Último bitmap estable conocido (None hasta la primera estabilización)."""
        return self._stable

    def set_stable_reads(self, stable_reads: int) -> None:
        """Cambia el debounce en caliente (ajuste desde la UI)."""
        if stable_reads < 1:
            raise ValueError("stable_reads debe ser >= 1")
        self._required = int(stable_reads)
        self._candidate = None
        self._count = 0

    def feed(self, bitmap: Bitmap) -> Bitmap | None:
        """Procesa una lectura cruda.

        Devuelve el nuevo bitmap estable cuando un cambio se confirma
        (incluida la primera estabilización); None mientras tanto.
        """
        if bitmap == self._stable:
            # Volvió al estado estable: descartar candidato transitorio.
            self._candidate = None
            self._count = 0
            return None

        if bitmap == self._candidate:
            self._count += 1
        else:
            self._candidate = bitmap
            self._count = 1

        if self._count >= self._required:
            self._stable = self._candidate
            self._candidate = None
            self._count = 0
            return self._stable
        return None
