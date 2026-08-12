import chess
import pytest

from app.game_state import GameState


def test_initial_state():
    game = GameState()
    assert game.expected_bitmap == chess.Board().occupied
    assert game.is_human_turn  # humano juega blancas por defecto
    assert game.san_history == []
    assert game.outcome() is None


def test_apply_move_updates_history_and_bitmap():
    game = GameState()
    game.apply_move(chess.Move.from_uci("e2e4"))
    assert game.san_history == ["e4"]
    assert not game.expected_bitmap & (1 << chess.E2)
    assert game.expected_bitmap & (1 << chess.E4)
    assert not game.is_human_turn


def test_apply_illegal_move_raises():
    game = GameState()
    with pytest.raises(ValueError):
        game.apply_move(chess.Move.from_uci("e2e5"))


def test_mismatched_squares_for_resync():
    game = GameState()
    sensor = game.expected_bitmap & ~(1 << chess.D1)  # falta la dama blanca
    assert not game.is_synchronized(sensor)
    assert game.mismatched_squares(sensor) == [chess.D1]
    assert game.is_synchronized(game.expected_bitmap)


def test_checkmate_outcome():
    game = GameState()
    for san in ["f3", "e5", "g4", "Qh4"]:  # mate del loco
        game.apply_move(game.board.parse_san(san))
    outcome = game.outcome()
    assert outcome is not None
    assert outcome.termination == "CHECKMATE"
    assert outcome.winner == chess.BLACK
    assert outcome.result == "0-1"


def test_reset():
    game = GameState()
    game.apply_move(chess.Move.from_uci("e2e4"))
    game.reset()
    assert game.expected_bitmap == chess.Board().occupied
    assert game.san_history == []
