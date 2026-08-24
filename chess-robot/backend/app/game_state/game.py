"""Núcleo del estado de partida.

Los sensores solo detectan presencia, no identidad. Partiendo de la posición
inicial conocida, cada jugada legal aplicada mantiene el mapa casilla→pieza en
``chess.Board``; el bitmap de ocupación esperado es ``board.occupied``.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from app.board_sensor.bitmap import Bitmap, diff_bitmaps


@dataclass(frozen=True)
class GameOutcome:
    """Resultado de partida terminada, en formato listo para la UI."""

    termination: str  # p.ej. "CHECKMATE", "STALEMATE"
    winner: chess.Color | None
    result: str  # "1-0", "0-1", "1/2-1/2"


class GameState:
    """Mantiene la partida y expone el estado que consumen los demás módulos."""

    def __init__(self, human_color: chess.Color = chess.WHITE) -> None:
        self.board = chess.Board()
        self.human_color = human_color
        # Reclamar tablas automáticamente (triple repetición / 50 jugadas).
        # Contra un humano no se usa; en la demo robot vs robot evita que la
        # partida se eternice en un final repetitivo.
        self.claim_draw = False
        self._san_history: list[str] = []

    # ------------------------------------------------------------------ estado

    @property
    def expected_bitmap(self) -> Bitmap:
        """Ocupación que deberían reportar los sensores para la posición actual."""
        return self.board.occupied

    @property
    def turn(self) -> chess.Color:
        return self.board.turn

    @property
    def is_human_turn(self) -> bool:
        return self.board.turn == self.human_color

    @property
    def san_history(self) -> list[str]:
        return list(self._san_history)

    @property
    def fen(self) -> str:
        return self.board.fen()

    def piece_at(self, square: chess.Square) -> chess.Piece | None:
        return self.board.piece_at(square)

    def legal_moves(self) -> list[chess.Move]:
        return list(self.board.legal_moves)

    def outcome(self) -> GameOutcome | None:
        outcome = self.board.outcome(claim_draw=self.claim_draw)
        if outcome is None:
            return None
        return GameOutcome(
            termination=outcome.termination.name,
            winner=outcome.winner,
            result=outcome.result(),
        )

    # ----------------------------------------------------------------- jugadas

    def apply_move(self, move: chess.Move) -> None:
        """Aplica una jugada legal (humana o del motor) y actualiza el historial."""
        if move not in self.board.legal_moves:
            raise ValueError(f"Jugada ilegal en la posición actual: {move.uci()}")
        self._san_history.append(self.board.san(move))
        self.board.push(move)

    # ------------------------------------------------------------------ resync

    def mismatched_squares(self, sensor_bitmap: Bitmap) -> list[chess.Square]:
        """Casillas donde los sensores no coinciden con la posición esperada.

        Base del modo *resync*: la UI resalta estas casillas hasta que el
        operador restaure las piezas.
        """
        diff = diff_bitmaps(self.expected_bitmap, sensor_bitmap)
        return sorted(diff.vacated + diff.occupied)

    def is_synchronized(self, sensor_bitmap: Bitmap) -> bool:
        return sensor_bitmap == self.expected_bitmap

    def reset(self) -> None:
        self.board.reset()
        self._san_history.clear()
