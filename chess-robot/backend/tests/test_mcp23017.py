"""Tests del driver MCP23017 con un bus I2C falso (sin hardware)."""

import chess
import pytest

from app.board_sensor.mcp23017 import (
    DEFAULT_ADDRESSES,
    MCP23017Driver,
    _GPIOA,
    _GPIOB,
    _GPPUA,
    _GPPUB,
    _IODIRA,
    _IODIRB,
)


class FakeBus:
    def __init__(self) -> None:
        self.registers: dict[tuple[int, int], int] = {}
        self.writes: list[tuple[int, int, int]] = []
        self.closed = False

    def write_byte_data(self, addr: int, register: int, value: int) -> None:
        self.writes.append((addr, register, value))
        self.registers[(addr, register)] = value

    def read_byte_data(self, addr: int, register: int) -> int:
        return self.registers.get((addr, register), 0)

    def close(self) -> None:
        self.closed = True


def set_gpio(bus: FakeBus, addr: int, port_a: int, port_b: int) -> None:
    bus.registers[(addr, _GPIOA)] = port_a
    bus.registers[(addr, _GPIOB)] = port_b


def test_configures_all_chips_as_inputs_with_pullups():
    bus = FakeBus()
    MCP23017Driver(bus)
    for addr in DEFAULT_ADDRESSES:
        for register in (_IODIRA, _IODIRB, _GPPUA, _GPPUB):
            assert bus.registers[(addr, register)] == 0xFF


def test_requires_exactly_four_chips():
    with pytest.raises(ValueError):
        MCP23017Driver(FakeBus(), addresses=(0x20, 0x21))


def test_active_low_mapping_to_squares():
    bus = FakeBus()
    # Todo en alto = sin imanes (activo en bajo) → tablero vacío.
    for addr in DEFAULT_ADDRESSES:
        set_gpio(bus, addr, 0xFF, 0xFF)
    driver = MCP23017Driver(bus)
    assert driver.read() == 0

    # Chip 0 puerto A bit 0 en bajo → a1. Chip 3 puerto B bit 7 en bajo → h8.
    set_gpio(bus, DEFAULT_ADDRESSES[0], 0xFE, 0xFF)
    set_gpio(bus, DEFAULT_ADDRESSES[3], 0xFF, 0x7F)
    bitmap = driver.read()
    assert bitmap == (1 << chess.A1) | (1 << chess.H8)


def test_full_starting_position():
    bus = FakeBus()
    # Filas 1-2 (chip 0) y 7-8 (chip 3) ocupadas: pines en bajo (0x00).
    set_gpio(bus, DEFAULT_ADDRESSES[0], 0x00, 0x00)
    set_gpio(bus, DEFAULT_ADDRESSES[1], 0xFF, 0xFF)
    set_gpio(bus, DEFAULT_ADDRESSES[2], 0xFF, 0xFF)
    set_gpio(bus, DEFAULT_ADDRESSES[3], 0x00, 0x00)
    driver = MCP23017Driver(bus)
    assert driver.read() == chess.Board().occupied


def test_close_closes_bus():
    bus = FakeBus()
    driver = MCP23017Driver(bus)
    driver.close()
    assert bus.closed
