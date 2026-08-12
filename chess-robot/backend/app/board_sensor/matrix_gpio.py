"""Driver alternativo: barrido de matriz por GPIO directo con libgpiod v2.

Solo aplica si se descarta la opción MCP23017. Requiere 8 líneas de fila
(salidas, con diodos por sensor) y 8 de columna (entradas con pull-down).
Pendiente de validar con hardware real; en Pi 5 usar el chip ``gpiochip4``.
"""

from __future__ import annotations

from app.board_sensor.bitmap import Bitmap

DEFAULT_CHIP = "/dev/gpiochip4"  # Pi 5: header de 40 pines


class MatrixGPIODriver:
    def __init__(
        self,
        row_pins: tuple[int, ...],
        col_pins: tuple[int, ...],
        chip_path: str = DEFAULT_CHIP,
    ) -> None:
        if len(row_pins) != 8 or len(col_pins) != 8:
            raise ValueError("Se requieren 8 pines de fila y 8 de columna")
        import gpiod  # import diferido: libgpiod v2 solo está en la Pi
        from gpiod.line import Bias, Direction, Value

        self._gpiod = gpiod
        self._Value = Value
        self._row_pins = row_pins
        self._col_pins = col_pins

        settings_out = gpiod.LineSettings(
            direction=Direction.OUTPUT, output_value=Value.INACTIVE
        )
        settings_in = gpiod.LineSettings(
            direction=Direction.INPUT, bias=Bias.PULL_DOWN
        )
        self._request = gpiod.request_lines(
            chip_path,
            consumer="chess-board-matrix",
            config={row_pins: settings_out, col_pins: settings_in},
        )

    def read(self) -> Bitmap:
        bitmap: Bitmap = 0
        Value = self._Value
        for rank, row_pin in enumerate(self._row_pins):
            self._request.set_value(row_pin, Value.ACTIVE)
            values = self._request.get_values(list(self._col_pins))
            self._request.set_value(row_pin, Value.INACTIVE)
            for file, value in enumerate(values):
                if value == Value.ACTIVE:
                    bitmap |= 1 << (rank * 8 + file)
        return bitmap

    def close(self) -> None:
        self._request.release()
