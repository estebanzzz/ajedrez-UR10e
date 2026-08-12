"""Traducción de jugadas a secuencias del robot, verificada con SimulatedRobot."""

import chess
import pytest

from app.robot_controller import (
    BoardGeometry,
    MotionParams,
    Point3,
    RobotController,
    SimulatedRobot,
    TrayGrid,
)

BOARD = BoardGeometry(
    a1=Point3(0.40, -0.175, 0.02),
    h1=Point3(0.75, -0.175, 0.02),
    a8=Point3(0.40, 0.175, 0.02),
    h8=Point3(0.75, 0.175, 0.02),
)
CAPTURE_TRAY = TrayGrid(
    origin=Point3(0.90, -0.20, 0.02),
    col_step=Point3(0.05, 0.0, 0.0),
    row_step=Point3(0.0, 0.05, 0.0),
    cols=4,
    rows=8,
)
RESERVE_TRAY = TrayGrid(
    origin=Point3(0.90, 0.25, 0.02),
    col_step=Point3(0.05, 0.0, 0.0),
    row_step=Point3(0.0, 0.05, 0.0),
    cols=2,
    rows=1,
)


def make_controller() -> tuple[RobotController, SimulatedRobot]:
    robot = SimulatedRobot()
    controller = RobotController(robot, BOARD, CAPTURE_TRAY, RESERVE_TRAY)
    return controller, robot


def positions_visited(robot: SimulatedRobot) -> list[tuple[float, float]]:
    return [(a.pose.position.x, a.pose.position.y) for a in robot.moves]


def test_simple_move_sequence():
    controller, robot = make_controller()
    board = chess.Board()
    steps = controller.execute_move(board, chess.Move.from_uci("e2e4"))
    assert steps == ["mover: e2 → e4"]
    # pick: tránsito + aproximación + agarre + tránsito; place: tránsito + bajada + retirada
    visited = positions_visited(robot)
    e2 = BOARD.square_center(chess.E2)
    e4 = BOARD.square_center(chess.E4)
    assert visited[0] == (e2.x, e2.y)
    assert visited[-1] == (e4.x, e4.y)
    # Dos acciones de garra en el pick (abrir + cerrar) y una al soltar.
    grippers = [a for a in robot.actions if a.kind == "gripper"]
    assert len(grippers) == 3


def test_transit_height_above_king():
    controller, robot = make_controller()
    controller.execute_move(chess.Board(), chess.Move.from_uci("e2e4"))
    transit_moves = [a for a in robot.moves if a.speed == MotionParams().speed_travel]
    # Los traslados ocurren por encima del rey (95 mm) + margen sobre el tablero.
    for action in transit_moves:
        assert action.pose.position.z >= 0.02 + 0.095 + 0.04 - 1e-9


def test_capture_removes_piece_first():
    controller, robot = make_controller()
    board = chess.Board()
    for san in ["e4", "d5"]:
        board.push_san(san)
    steps = controller.execute_move(board, chess.Move.from_uci("e4d5"))
    assert steps[0].startswith("captura: d5 → bandeja")
    assert steps[1] == "mover: e4 → d5"
    assert controller.captures_used == 1
    # La primera posición visitada es la casilla de la pieza capturada.
    d5 = BOARD.square_center(chess.D5)
    assert positions_visited(robot)[0] == (d5.x, d5.y)
    # Y el primer place es el slot 0 de la bandeja.
    tray0 = CAPTURE_TRAY.slot_position(0)
    assert (tray0.x, tray0.y) in positions_visited(robot)


def test_en_passant_removes_correct_pawn():
    board = chess.Board()
    for san in ["e4", "a6", "e5", "d5"]:
        board.push_san(san)
    controller, robot = make_controller()
    steps = controller.execute_move(board, chess.Move.from_uci("e5d6"))
    assert steps[0].startswith("captura: d5")  # el peón está en d5, no en d6
    assert steps[1] == "mover: e5 → d6"


def test_castling_moves_king_then_rook():
    board = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]:
        board.push_san(san)
    controller, robot = make_controller()
    steps = controller.execute_move(board, board.parse_san("O-O"))
    assert steps == ["mover: e1 → g1", "enroque: torre h1 → f1"]


def test_promotion_uses_reserve_tray():
    board = chess.Board("8/P6k/8/8/8/8/7K/8 w - - 0 1")
    controller, robot = make_controller()
    steps = controller.execute_move(board, chess.Move.from_uci("a7a8q"))
    assert steps[0] == "promoción: peón a7 → bandeja"
    assert steps[1] == "promoción: queen de reserva → a8"
    assert controller.captures_used == 1  # el peón ocupó un slot de capturas
    assert controller.reserve_used == 1


def test_promotion_with_capture():
    board = chess.Board("1r5k/P7/8/8/8/8/7K/8 w - - 0 1")
    controller, robot = make_controller()
    steps = controller.execute_move(board, chess.Move.from_uci("a7b8q"))
    assert steps[0].startswith("captura: b8")
    assert controller.captures_used == 2  # torre capturada + peón promovido


def test_illegal_move_rejected():
    controller, _ = make_controller()
    with pytest.raises(ValueError):
        controller.execute_move(chess.Board(), chess.Move.from_uci("e2e5"))


def test_full_game_capture_tray_never_overflows():
    """Partida al azar completa: la bandeja 4x8 alcanza para las 30 capturas máx."""
    import random

    rng = random.Random(7)
    board = chess.Board()
    controller, _ = make_controller()
    for _ in range(200):
        if board.is_game_over():
            break
        move = rng.choice(list(board.legal_moves))
        # Saltar promociones: la reserva simulada solo tiene 2 damas.
        if move.promotion and move.promotion != chess.QUEEN:
            continue
        if move.promotion and controller.reserve_used >= RESERVE_TRAY.capacity:
            continue
        controller.execute_move(board, move)
        board.push(move)
    assert controller.captures_used <= CAPTURE_TRAY.capacity
