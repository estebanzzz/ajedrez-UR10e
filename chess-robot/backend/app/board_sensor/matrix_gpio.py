"""Driver de la matriz 8x8 conectada directamente a los GPIO de la Pi.

Cableado (documentar en firmware-docs/ al armar la electrónica):
- 8 líneas de **fila** como salidas: fila i energiza los sensores del rank i+1.
- 8 líneas de **columna** como entradas con pull-down: bit j = columna a..h.
- **Diodo en serie por sensor** para evitar lecturas fantasma (ghosting)
  cuando hay 3+ piezas formando una "L".

Barrido: se activa una fila, se deja asentar la señal, se leen las 8
columnas, se desactiva. 8 filas por lectura completa → a 30 Hz de escaneo
son ~240 activaciones/s, sin problema para libgpiod.

La lógica de barrido está separada del acceso físico (``MatrixBackend``)
para poder testearla sin hardware; en la Pi se usa ``GpiodBackend``
(libgpiod v2 — RPi.GPIO no funciona en Pi 5).
"""

from __future__ import annotations

import time
from typing import Protocol

from app.board_sensor.bitmap import Bitmap

DEFAULT_CHIP = "/dev/gpiochip4"  # Pi 5: header de 40 pines

# Pines BCM por defecto (16 GPIO libres, sin conflicto con I2C/SPI/UART).
DEFAULT_ROW_PINS = (4, 17, 27, 22, 5, 6, 13, 19)  # filas 1..8 (salidas)
DEFAULT_COL_PINS = (12, 16, 20, 21, 23, 24, 25, 26)  # columnas a..h (entradas)


class MatrixBackend(Protocol):
    """Acceso físico a las líneas de la matriz (real o simulado)."""

    def set_row(self, row: int, active: bool) -> None:
        """Energiza/desenergiza la fila (0..7)."""
        ...

    def read_columns(self) -> int:
        """Lee las 8 columnas: bit j (0..7) = columna a..h activa."""
        ...

    def close(self) -> None: ...


class MatrixGPIODriver:
    def __init__(self, backend: MatrixBackend, settle_s: float = 50e-6) -> None:
        self._backend = backend
        self._settle_s = settle_s

    def read(self) -> Bitmap:
        bitmap: Bitmap = 0
        for row in range(8):
            self._backend.set_row(row, True)
            if self._settle_s:
                time.sleep(self._settle_s)
            columns = self._backend.read_columns() & 0xFF
            self._backend.set_row(row, False)
            bitmap |= columns << (row * 8)
        return bitmap

    def close(self) -> None:
        self._backend.close()


class GpiodBackend:
    """Backend real con libgpiod v2 (solo disponible en la Pi)."""

    def __init__(
        self,
        row_pins: tuple[int, ...] = DEFAULT_ROW_PINS,
        col_pins: tuple[int, ...] = DEFAULT_COL_PINS,
        chip_path: str = DEFAULT_CHIP,
    ) -> None:
        if len(row_pins) != 8 or len(col_pins) != 8:
            raise ValueError("Se requieren 8 pines de fila y 8 de columna")
        import gpiod  # import diferido: libgpiod v2 solo está en la Pi
        from gpiod.line import Bias, Direction, Value

        self._Value = Value
        self._row_pins = row_pins
        self._col_pins = list(col_pins)

        self._request = gpiod.request_lines(
            chip_path,
            consumer="chess-board-matrix",
            config={
                row_pins: gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE
                ),
                col_pins: gpiod.LineSettings(
                    direction=Direction.INPUT, bias=Bias.PULL_DOWN
                ),
            },
        )

    def set_row(self, row: int, active: bool) -> None:
        value = self._Value.ACTIVE if active else self._Value.INACTIVE
        self._request.set_value(self._row_pins[row], value)

    def read_columns(self) -> int:
        values = self._request.get_values(self._col_pins)
        result = 0
        for bit, value in enumerate(values):
            if value == self._Value.ACTIVE:
                result |= 1 << bit
        return result

    def close(self) -> None:
        self._request.release()
