"""Driver de la matriz 8x8 con 4× MCP23017 por I2C (opción recomendada).

Cableado asumido (documentar en firmware-docs/ cuando se arme la electrónica):
- Chip i (dirección 0x20+i) cubre las filas 2i+1 y 2i+2 del tablero:
  puerto A = fila 2i+1 (bit 0 = columna a … bit 7 = columna h),
  puerto B = fila 2i+2 con la misma convención.
- Sensores reed/hall a masa con pull-up interno → activo en bajo
  (``active_low=True``): imán presente = 0 en el pin.

El bus I2C se inyecta para poder testear sin hardware; en la Pi se usa
``smbus2.SMBus(1)``.
"""

from __future__ import annotations

from typing import Protocol

from app.board_sensor.bitmap import Bitmap

# Registros con IOCON.BANK = 0 (default tras reset)
_IODIRA = 0x00
_IODIRB = 0x01
_GPPUA = 0x0C
_GPPUB = 0x0D
_GPIOA = 0x12
_GPIOB = 0x13

DEFAULT_ADDRESSES = (0x20, 0x21, 0x22, 0x23)


class I2CBus(Protocol):
    def write_byte_data(self, addr: int, register: int, value: int) -> None: ...
    def read_byte_data(self, addr: int, register: int) -> int: ...
    def close(self) -> None: ...


def open_smbus(bus_number: int = 1) -> I2CBus:
    """Abre el bus I2C real de la Pi (import diferido: smbus2 solo está allí)."""
    import smbus2

    return smbus2.SMBus(bus_number)


class MCP23017Driver:
    def __init__(
        self,
        bus: I2CBus,
        addresses: tuple[int, ...] = DEFAULT_ADDRESSES,
        active_low: bool = True,
    ) -> None:
        if len(addresses) != 4:
            raise ValueError("Se requieren 4 MCP23017 (16 casillas cada uno)")
        self._bus = bus
        self._addresses = addresses
        self._active_low = active_low
        self._configure()

    def _configure(self) -> None:
        for addr in self._addresses:
            # Todos los pines como entrada con pull-up interno.
            self._bus.write_byte_data(addr, _IODIRA, 0xFF)
            self._bus.write_byte_data(addr, _IODIRB, 0xFF)
            self._bus.write_byte_data(addr, _GPPUA, 0xFF)
            self._bus.write_byte_data(addr, _GPPUB, 0xFF)

    def read(self) -> Bitmap:
        bitmap: Bitmap = 0
        for chip, addr in enumerate(self._addresses):
            port_a = self._bus.read_byte_data(addr, _GPIOA)
            port_b = self._bus.read_byte_data(addr, _GPIOB)
            if self._active_low:
                port_a = ~port_a & 0xFF
                port_b = ~port_b & 0xFF
            # Puerto A = fila 2*chip (índice 0), puerto B = fila siguiente.
            bitmap |= port_a << (16 * chip)
            bitmap |= port_b << (16 * chip + 8)
        return bitmap

    def close(self) -> None:
        self._bus.close()
