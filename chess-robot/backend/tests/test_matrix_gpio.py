"""Tests del barrido de matriz con un backend simulado (sin hardware)."""

import chess

from app.board_sensor.matrix_gpio import MatrixGPIODriver


class FakeBackend:
    """Simula la física de la matriz: solo la fila energizada devuelve columnas."""

    def __init__(self, occupied: set[int] | None = None) -> None:
        self.occupied = occupied or set()  # casillas 0..63
        self._active_row: int | None = None
        self.activations: list[int] = []
        self.closed = False

    def set_row(self, row: int, active: bool) -> None:
        if active:
            self._active_row = row
            self.activations.append(row)
        elif self._active_row == row:
            self._active_row = None

    def read_columns(self) -> int:
        if self._active_row is None:
            return 0
        result = 0
        for file in range(8):
            if self._active_row * 8 + file in self.occupied:
                result |= 1 << file
        return result

    def close(self) -> None:
        self.closed = True


def make_driver(occupied: set[int]) -> tuple[MatrixGPIODriver, FakeBackend]:
    backend = FakeBackend(occupied)
    return MatrixGPIODriver(backend, settle_s=0), backend


def test_empty_board():
    driver, _ = make_driver(set())
    assert driver.read() == 0


def test_single_squares_map_correctly():
    driver, _ = make_driver({chess.A1, chess.E4, chess.H8})
    assert driver.read() == (1 << chess.A1) | (1 << chess.E4) | (1 << chess.H8)


def test_full_starting_position():
    occupied = set(chess.scan_forward(chess.Board().occupied))
    driver, _ = make_driver(occupied)
    assert driver.read() == chess.Board().occupied


def test_scans_all_rows_each_read():
    driver, backend = make_driver(set())
    driver.read()
    assert backend.activations == list(range(8))
    # Ninguna fila queda energizada tras el barrido.
    assert backend._active_row is None


def test_close_releases_backend():
    driver, backend = make_driver(set())
    driver.close()
    assert backend.closed
