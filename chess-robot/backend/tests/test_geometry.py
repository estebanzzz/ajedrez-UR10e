import chess
import pytest

from app.robot_controller.geometry import BoardGeometry, Point3, TrayGrid

# Tablero de 40 cm (casillas de 50 mm) en un plano simple para verificar a ojo:
# a1 en (0.40, -0.175), filas hacia +y, columnas hacia +x.
BOARD = BoardGeometry(
    a1=Point3(0.40, -0.175, 0.02),
    h1=Point3(0.75, -0.175, 0.02),
    a8=Point3(0.40, 0.175, 0.02),
    h8=Point3(0.75, 0.175, 0.02),
)


def test_corners_map_to_themselves():
    assert BOARD.square_center(chess.A1) == BOARD.a1
    assert BOARD.square_center(chess.H1) == BOARD.h1
    assert BOARD.square_center(chess.A8) == BOARD.a8
    assert BOARD.square_center(chess.H8) == BOARD.h8


def test_center_squares_interpolate():
    e4 = BOARD.square_center(chess.E4)
    # e = columna 4/7, fila 3/7 del rango [0.40..0.75] x [-0.175..0.175]
    assert e4.x == pytest.approx(0.40 + 0.35 * 4 / 7)
    assert e4.y == pytest.approx(-0.175 + 0.35 * 3 / 7)
    assert e4.z == pytest.approx(0.02)


def test_tilted_board_interpolates_z():
    tilted = BoardGeometry(
        a1=Point3(0.4, -0.175, 0.020),
        h1=Point3(0.75, -0.175, 0.024),
        a8=Point3(0.4, 0.175, 0.020),
        h8=Point3(0.75, 0.175, 0.024),
    )
    # d4 está a 3/7 del recorrido en x: z interpola linealmente.
    d4 = tilted.square_center(chess.D4)
    assert d4.z == pytest.approx(0.020 + 0.004 * 3 / 7)
    assert tilted.max_z == 0.024


def test_tray_slots_advance_columns_then_rows():
    tray = TrayGrid(
        origin=Point3(0.9, -0.2, 0.02),
        col_step=Point3(0.05, 0.0, 0.0),
        row_step=Point3(0.0, 0.05, 0.0),
        cols=4,
        rows=8,
    )
    assert tray.capacity == 32
    assert tray.slot_position(0) == tray.origin
    slot5 = tray.slot_position(5)  # fila 1, columna 1
    assert slot5.x == pytest.approx(0.95)
    assert slot5.y == pytest.approx(-0.15)
    with pytest.raises(IndexError):
        tray.slot_position(32)
