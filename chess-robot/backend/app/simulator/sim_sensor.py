"""Simulador del tablero sensorizado: genera los snapshots de bitmap que
produciría un humano ejecutando físicamente una jugada, incluidos los estados
intermedios (piezas en el aire)."""

from __future__ import annotations

import chess

from app.board_sensor.bitmap import Bitmap


def snapshots_for_move(board: chess.Board, move: chess.Move) -> list[Bitmap]:
    """Secuencia de bitmaps intermedios y final para una jugada legal.

    El último snapshot es siempre la ocupación resultante de la jugada.
    """
    start: Bitmap = board.occupied
    snapshots: list[Bitmap] = []

    if board.is_castling(move):
        # Rey primero, luego torre (orden típico y reglamentario).
        king_from, king_to = move.from_square, move.to_square
        rook_from = chess.H1 if chess.square_file(king_to) == 6 else chess.A1
        if board.turn == chess.BLACK:
            rook_from += 56
        rook_to = (king_from + king_to) // 2
        current = start & ~(1 << king_from)  # rey en el aire
        snapshots.append(current)
        current |= 1 << king_to
        snapshots.append(current)
        current &= ~(1 << rook_from)  # torre en el aire
        snapshots.append(current)
        current |= 1 << rook_to
        snapshots.append(current)
        return snapshots

    if board.is_en_passant(move):
        captured = chess.square(
            chess.square_file(move.to_square), chess.square_rank(move.from_square)
        )
        current = start & ~(1 << move.from_square)
        snapshots.append(current)
        current |= 1 << move.to_square
        snapshots.append(current)
        current &= ~(1 << captured)  # retirar el peón capturado
        snapshots.append(current)
        return snapshots

    if board.is_capture(move):
        # El humano suele retirar primero la pieza rival.
        current = start & ~(1 << move.to_square)
        snapshots.append(current)
        current &= ~(1 << move.from_square)
        snapshots.append(current)
        current |= 1 << move.to_square
        snapshots.append(current)
        return snapshots

    # Jugada simple (incluye promoción sin captura: sale el peón, entra la pieza).
    current = start & ~(1 << move.from_square)
    snapshots.append(current)
    current |= 1 << move.to_square
    snapshots.append(current)
    return snapshots
