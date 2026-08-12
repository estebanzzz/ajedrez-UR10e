import chess
import pytest

from app.engine import (
    DIFFICULTY_PRESETS,
    Evaluation,
    RandomEngine,
    StockfishEngine,
    find_stockfish,
)

STOCKFISH = find_stockfish()


def test_presets_cover_expected_levels():
    assert set(DIFFICULTY_PRESETS) == {"principiante", "intermedio", "avanzado", "maximo"}
    assert DIFFICULTY_PRESETS["maximo"].elo is None


def test_evaluation_formatting():
    assert str(Evaluation(centipawns=134, mate_in=None)) == "+1.34"
    assert str(Evaluation(centipawns=None, mate_in=3)) == "M3"


def test_random_engine_plays_legal_moves():
    with RandomEngine(seed=1) as engine:
        board = chess.Board()
        for _ in range(20):
            move = engine.choose_move(board)
            assert move in board.legal_moves
            board.push(move)


@pytest.mark.skipif(STOCKFISH is None, reason="Stockfish no está en el PATH")
def test_stockfish_plays_and_evaluates():
    with StockfishEngine(STOCKFISH, difficulty="principiante") as engine:
        board = chess.Board()
        move = engine.choose_move(board)
        assert move in board.legal_moves
        evaluation = engine.evaluate(board, time_limit=0.1)
        assert evaluation.centipawns is not None or evaluation.mate_in is not None


@pytest.mark.skipif(STOCKFISH is None, reason="Stockfish no está en el PATH")
def test_stockfish_difficulty_change():
    with StockfishEngine(STOCKFISH, difficulty="principiante") as engine:
        engine.set_difficulty("maximo")
        assert engine.difficulty == "maximo"
        with pytest.raises(ValueError):
            engine.set_difficulty("imposible")
