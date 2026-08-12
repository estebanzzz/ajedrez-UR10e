"""Parámetros de manipulación por tipo de pieza.

Valores por defecto para un juego Staunton nº 6 aproximado — se ajustan en
calibración según las piezas reales y se persisten con ``CalibrationStore``.
Fuerza baja: piezas livianas y entorno público.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class PieceParams:
    height_m: float  # altura total de la pieza
    grip_height_m: float  # altura del punto de agarre sobre la casilla
    grip_opening_mm: float  # apertura de garra al cerrar sobre la pieza
    approach_opening_mm: float  # apertura antes de descender
    grip_force: float  # 0..1 relativo (mapear al rango de la garra)


DEFAULT_PIECE_PARAMS: dict[chess.PieceType, PieceParams] = {
    chess.PAWN: PieceParams(0.045, 0.020, 28.0, 50.0, 0.25),
    chess.KNIGHT: PieceParams(0.055, 0.025, 32.0, 55.0, 0.25),
    chess.BISHOP: PieceParams(0.065, 0.028, 30.0, 55.0, 0.25),
    chess.ROOK: PieceParams(0.055, 0.025, 34.0, 55.0, 0.25),
    chess.QUEEN: PieceParams(0.080, 0.032, 34.0, 60.0, 0.30),
    chess.KING: PieceParams(0.095, 0.035, 34.0, 60.0, 0.30),
}


def tallest_piece_height(params: dict[chess.PieceType, PieceParams]) -> float:
    return max(p.height_m for p in params.values())
