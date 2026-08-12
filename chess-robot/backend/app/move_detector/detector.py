"""Máquina de estados que interpreta los cambios del bitmap en el turno humano.

Estrategia: como la identidad de las piezas se conoce por seguimiento de
estado, cada jugada legal determina exactamente qué bitmap de ocupación
produce. Durante el turno se validan los estados intermedios (piezas en el
aire) contra las casillas que alguna jugada legal puede tocar; al confirmar,
el bitmap final se resuelve contra el conjunto de jugadas legales.

Casos cubiertos:
- Jugada simple: origen se vacía, destino se ocupa.
- Captura: la pieza rival puede levantarse antes o después de mover la propia.
- Enroque: 4 eventos (rey y torre), en cualquier orden.
- En passant: destino se ocupa y el peón capturado sale de otra casilla.
- Promoción: el bitmap no distingue la pieza elegida; se reportan candidatas
  y se usa dama por defecto (la pieza física sale de la bandeja de reserva).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

import chess

from app.board_sensor.bitmap import Bitmap, BitmapDiff, diff_bitmaps


class DetectorPhase(enum.Enum):
    IDLE = "idle"  # sin cambios respecto a la posición esperada
    IN_PROGRESS = "in_progress"  # cambios compatibles con alguna jugada legal
    COMPLETE = "complete"  # el bitmap actual coincide con una jugada legal
    INVALID = "invalid"  # cambios incompatibles con toda jugada legal


@dataclass(frozen=True)
class DetectionResult:
    """Jugada resuelta al confirmar."""

    move: chess.Move
    # En promociones el bitmap es idéntico para dama/torre/alfil/caballo:
    # se listan todas y `move` lleva la promoción por defecto (dama).
    promotion_candidates: tuple[chess.Move, ...] = ()

    @property
    def is_promotion(self) -> bool:
        return bool(self.promotion_candidates)


@dataclass(frozen=True)
class DetectionError:
    """El bitmap confirmado no corresponde a ninguna jugada legal."""

    diff: BitmapDiff  # casillas cambiadas respecto a la posición esperada
    message: str = "El tablero no coincide con ninguna jugada legal."

    @property
    def mismatched_squares(self) -> tuple[chess.Square, ...]:
        return tuple(sorted(self.diff.vacated + self.diff.occupied))


def _touched_squares(board: chess.Board, move: chess.Move) -> set[chess.Square]:
    """Casillas cuya ocupación puede cambiar transitoriamente durante la jugada."""
    squares = {move.from_square, move.to_square}
    if board.is_en_passant(move):
        # El peón capturado está en la fila del origen, columna del destino.
        squares.add(chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square)))
    if board.is_castling(move):
        rook_from = chess.H1 if chess.square_file(move.to_square) == 6 else chess.A1
        if board.turn == chess.BLACK:
            rook_from += 56  # misma columna, fila 8
        rook_to = (move.from_square + move.to_square) // 2  # casilla entre rey origen y destino
        squares.update({rook_from, rook_to})
    return squares


def _occupancy_after(board: chess.Board, move: chess.Move) -> Bitmap:
    board_copy = board.copy(stack=False)
    board_copy.push(move)
    return board_copy.occupied


class MoveDetector:
    """Interpreta snapshots del bitmap de sensores durante el turno humano."""

    def __init__(self, board: chess.Board) -> None:
        self._board = board
        self._baseline: Bitmap = board.occupied
        self._current: Bitmap = self._baseline
        # Casillas que cambiaron en algún momento del turno (desambigua capturas:
        # dos capturas desde el mismo origen dejan el mismo bitmap final, pero
        # la casilla del destino capturado se vacía transitoriamente).
        self._observed_touched: set[chess.Square] = set()
        # Precalculado por turno: jugada legal → (bitmap final, casillas tocadas)
        self._legal: dict[chess.Move, tuple[Bitmap, set[chess.Square]]] = {}
        self.begin_turn()

    def begin_turn(self) -> None:
        """Captura la posición esperada y precalcula las jugadas legales."""
        self._baseline = self._board.occupied
        self._current = self._baseline
        self._observed_touched = set()
        self._legal = {
            move: (_occupancy_after(self._board, move), _touched_squares(self._board, move))
            for move in self._board.legal_moves
        }

    # ----------------------------------------------------------------- estado

    @property
    def phase(self) -> DetectorPhase:
        if self._current == self._baseline:
            return DetectorPhase.IDLE
        if any(final == self._current for final, _ in self._legal.values()):
            return DetectorPhase.COMPLETE
        changed = self._changed_squares()
        if any(changed <= touched for _, touched in self._legal.values()):
            return DetectorPhase.IN_PROGRESS
        return DetectorPhase.INVALID

    @property
    def diff(self) -> BitmapDiff:
        return diff_bitmaps(self._baseline, self._current)

    def _changed_squares(self) -> set[chess.Square]:
        d = self.diff
        return set(d.vacated) | set(d.occupied)

    # ------------------------------------------------------------ interacción

    def update(self, sensor_bitmap: Bitmap) -> DetectorPhase:
        """Nuevo snapshot (ya con debounce aplicado). Devuelve la fase para la UI."""
        self._current = sensor_bitmap
        diff = diff_bitmaps(self._baseline, sensor_bitmap)
        self._observed_touched.update(diff.vacated, diff.occupied)
        return self.phase

    def confirm(self) -> DetectionResult | DetectionError:
        """El humano confirmó su jugada (botón): resolver el bitmap final."""
        matches = [
            move
            for move, (final, touched) in self._legal.items()
            if final == self._current and self._observed_touched <= touched
        ]
        if not matches:
            # Reintento sin el filtro de casillas observadas: si hay exactamente
            # una jugada cuyo bitmap final coincide, los eventos intermedios
            # espurios (p.ej. pieza acomodada y repuesta) no deben bloquearla.
            by_final = [
                move for move, (final, _) in self._legal.items() if final == self._current
            ]
            if len(by_final) == 1:
                matches = by_final
            else:
                message = (
                    "El tablero no coincide con ninguna jugada legal."
                    if not by_final
                    else "Jugada ambigua: no se pudo determinar la captura realizada."
                )
                return DetectionError(diff=self.diff, message=message)

        promotions = [move for move in matches if move.promotion]
        if promotions:
            # Mismo bitmap para toda pieza de promoción: dama por defecto.
            default = next(
                (m for m in promotions if m.promotion == chess.QUEEN), promotions[0]
            )
            return DetectionResult(move=default, promotion_candidates=tuple(promotions))

        if len(matches) > 1:
            # Solo posible si los sensores no registraron el estado intermedio
            # que distingue dos capturas desde el mismo origen.
            return DetectionError(
                diff=self.diff,
                message="Jugada ambigua: no se pudo determinar la captura realizada.",
            )
        return DetectionResult(move=matches[0])
