"""Detección de jugadas a partir de diffs de bitmap — el corazón de la Fase 1.

Cada test reproduce la secuencia física de snapshots (piezas en el aire
incluidas) que generaría un humano, usando el simulador de sensores.
"""

import chess
import pytest

from app.board_sensor.bitmap import bitmap_from_squares
from app.move_detector import (
    DetectionAmbiguity,
    DetectionError,
    DetectionResult,
    DetectorPhase,
    MoveDetector,
)
from app.simulator.sim_sensor import snapshots_for_move


def detect(board: chess.Board, move: chess.Move) -> DetectionResult | DetectionError:
    """Pasa los snapshots simulados de una jugada por el detector y confirma."""
    detector = MoveDetector(board)
    for snapshot in snapshots_for_move(board, move):
        detector.update(snapshot)
    return detector.confirm()


def board_from_moves(*sans: str) -> chess.Board:
    board = chess.Board()
    for san in sans:
        board.push_san(san)
    return board


# ------------------------------------------------------------- jugadas simples


def test_simple_pawn_move():
    board = chess.Board()
    result = detect(board, chess.Move.from_uci("e2e4"))
    assert isinstance(result, DetectionResult)
    assert result.move == chess.Move.from_uci("e2e4")


def test_knight_move():
    board = chess.Board()
    result = detect(board, chess.Move.from_uci("g1f3"))
    assert isinstance(result, DetectionResult)
    assert result.move == chess.Move.from_uci("g1f3")


def test_intermediate_phase_piece_lifted():
    board = chess.Board()
    detector = MoveDetector(board)
    lifted = board.occupied & ~(1 << chess.E2)
    assert detector.update(lifted) == DetectorPhase.IN_PROGRESS


def test_phase_complete_when_bitmap_matches_legal_move():
    board = chess.Board()
    detector = MoveDetector(board)
    final = (board.occupied & ~(1 << chess.E2)) | (1 << chess.E4)
    assert detector.update(final) == DetectorPhase.COMPLETE


# ------------------------------------------------------------------- capturas


def test_capture():
    board = board_from_moves("e4", "d5")
    result = detect(board, chess.Move.from_uci("e4d5"))
    assert isinstance(result, DetectionResult)
    assert result.move == chess.Move.from_uci("e4d5")


def test_capture_intermediate_states_are_valid():
    """Retirar la pieza rival primero deja el destino vacío transitoriamente."""
    board = board_from_moves("e4", "d5")
    detector = MoveDetector(board)
    phases = [detector.update(s) for s in snapshots_for_move(board, chess.Move.from_uci("e4d5"))]
    assert phases[0] == DetectorPhase.IN_PROGRESS  # d5 vacía (pieza rival retirada)
    assert phases[-1] == DetectorPhase.COMPLETE


# -------------------------------------------------------------------- enroque


def test_castling_kingside():
    board = board_from_moves("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5")
    move = board.parse_san("O-O")
    result = detect(board, move)
    assert isinstance(result, DetectionResult)
    assert result.move == move


def test_castling_queenside():
    board = board_from_moves("d4", "d5", "Nc3", "Nc6", "Bf4", "Bf5", "Qd2", "Qd7")
    move = board.parse_san("O-O-O")
    result = detect(board, move)
    assert isinstance(result, DetectionResult)
    assert result.move == move


def test_castling_black():
    board = board_from_moves("e4", "e5", "Nf3", "Nf6", "Bc4", "Bc5", "d3")
    move = board.parse_san("O-O")
    result = detect(board, move)
    assert isinstance(result, DetectionResult)
    assert result.move == move


def test_castling_all_intermediate_states_valid():
    board = board_from_moves("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5")
    detector = MoveDetector(board)
    move = board.parse_san("O-O")
    phases = [detector.update(s) for s in snapshots_for_move(board, move)]
    assert DetectorPhase.INVALID not in phases
    assert phases[-1] == DetectorPhase.COMPLETE


# ----------------------------------------------------------------- en passant


def test_en_passant():
    board = board_from_moves("e4", "a6", "e5", "d5")
    move = chess.Move.from_uci("e5d6")
    assert board.is_en_passant(move)
    result = detect(board, move)
    assert isinstance(result, DetectionResult)
    assert result.move == move


# ------------------------------------------------------------------ promoción


def _promotion_board() -> chess.Board:
    # Peón blanco en a7 listo para coronar.
    return chess.Board("8/P6k/8/8/8/8/7K/8 w - - 0 1")


def test_promotion_defaults_to_queen():
    board = _promotion_board()
    result = detect(board, chess.Move.from_uci("a7a8q"))
    assert isinstance(result, DetectionResult)
    assert result.is_promotion
    assert result.move.promotion == chess.QUEEN
    ucis = {m.uci() for m in result.promotion_candidates}
    assert ucis == {"a7a8q", "a7a8r", "a7a8b", "a7a8n"}


def test_promotion_with_capture():
    board = chess.Board("1r6/P6k/8/8/8/8/7K/8 w - - 0 1")
    result = detect(board, chess.Move.from_uci("a7b8q"))
    assert isinstance(result, DetectionResult)
    assert result.is_promotion
    assert result.move == chess.Move.from_uci("a7b8q")


# ------------------------------------------- capturas ambiguas (mismo origen)


def _two_capture_board() -> chess.Board:
    # Dama blanca en d4 puede capturar en d6 o en f6: ambas dejan el mismo
    # bitmap final (solo se vacía d4), porque el destino ya estaba ocupado.
    return chess.Board("k7/8/3p1p2/8/3Q4/8/8/K7 w - - 0 1")


def test_ambiguous_captures_resolved_by_intermediate_states():
    board = _two_capture_board()
    for uci in ("d4d6", "d4f6"):
        move = chess.Move.from_uci(uci)
        result = detect(board, move)
        assert isinstance(result, DetectionResult)
        assert result.move == move


def test_ambiguous_captures_without_intermediates_returns_candidates():
    """Sin estado intermedio la jugada NO es ilegal: se devuelven las
    candidatas para que el humano elija en la UI (caso real: Nxh7/Nxf7 con
    la mano ocluyendo el destino durante el cambio de piezas)."""
    board = _two_capture_board()
    detector = MoveDetector(board)
    # Los sensores solo entregan el bitmap final (se perdió el estado intermedio).
    detector.update(board.occupied & ~(1 << chess.D4))
    result = detector.confirm()
    assert isinstance(result, DetectionAmbiguity)
    assert {m.uci() for m in result.candidates} == {"d4d6", "d4f6"}


# ------------------------------------------------------- errores y resync


def test_illegal_final_position_returns_error():
    board = chess.Board()
    detector = MoveDetector(board)
    # e2 → e5 no es jugada legal de peón.
    final = (board.occupied & ~(1 << chess.E2)) | (1 << chess.E5)
    detector.update(final)
    result = detector.confirm()
    assert isinstance(result, DetectionError)
    assert chess.E2 in result.mismatched_squares
    assert chess.E5 in result.mismatched_squares


def test_touching_unrelated_squares_is_invalid_phase():
    board = chess.Board()
    detector = MoveDetector(board)
    # Quitar una pieza rival (torre negra a8) no es parte de ninguna jugada blanca.
    phase = detector.update(board.occupied & ~(1 << chess.A8))
    assert phase == DetectorPhase.INVALID


def test_confirm_without_changes_is_error():
    board = chess.Board()
    detector = MoveDetector(board)
    result = detector.confirm()
    assert isinstance(result, DetectionError)


def test_full_random_games_detected_end_to_end():
    """Partidas completas al azar: toda jugada debe detectarse por sensores."""
    import random

    rng = random.Random(42)
    for _ in range(5):
        board = chess.Board()
        for _ply in range(120):
            if board.is_game_over():
                break
            move = rng.choice(list(board.legal_moves))
            result = detect(board, move)
            assert isinstance(result, DetectionResult), (
                f"No detectada: {move.uci()} en {board.fen()}"
            )
            if move.promotion:
                # El bitmap no distingue la pieza: debe estar entre las candidatas.
                assert move in result.promotion_candidates
                board.push(move)  # aplicar la intención real del jugador
            else:
                assert result.move == move
                board.push(result.move)
