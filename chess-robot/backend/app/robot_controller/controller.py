"""Traducción de jugadas UCI a secuencias pick & place del robot.

Patrón de cada manipulación (spec 3.2.5):
aproximación a altura segura → descenso → cierre de garra → ascenso a altura
de tránsito (por encima del rey) → traslado → descenso → apertura → retirada.

Orden por tipo de jugada:
- Captura (incl. en passant): primero la pieza capturada a la bandeja.
- Enroque: rey y luego torre.
- Promoción: peón a la bandeja de capturas, dama desde la reserva.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from app.robot_controller.geometry import BoardGeometry, Point3, TrayGrid
from app.robot_controller.pieces import (
    DEFAULT_PIECE_PARAMS,
    PieceParams,
    tallest_piece_height,
)
from app.robot_controller.robot import Pose, RobotInterface


@dataclass(frozen=True)
class MotionParams:
    """Velocidades reducidas por defecto: entorno público (spec sección 4)."""

    speed_travel: float = 0.25  # m/s entre casillas a altura de tránsito
    speed_vertical: float = 0.08  # m/s en descensos/ascensos
    acceleration: float = 0.4  # m/s^2
    approach_clearance_m: float = 0.06  # altura de aproximación sobre la pieza
    transit_clearance_m: float = 0.04  # margen sobre la pieza más alta


@dataclass
class TrayState:
    """Ocupación de una bandeja en grilla."""

    tray: TrayGrid
    used: int = 0

    def next_slot(self) -> Point3:
        if self.used >= self.tray.capacity:
            raise RuntimeError("Bandeja llena")
        position = self.tray.slot_position(self.used)
        self.used += 1
        return position

    def take_slot(self) -> Point3:
        """Retira una pieza de la bandeja (reserva de promoción)."""
        if self.used >= self.tray.capacity:
            raise RuntimeError("Bandeja de reserva agotada")
        position = self.tray.slot_position(self.used)
        self.used += 1
        return position


class RobotController:
    def __init__(
        self,
        robot: RobotInterface,
        geometry: BoardGeometry,
        capture_tray: TrayGrid,
        reserve_tray: TrayGrid,
        piece_params: dict[chess.PieceType, PieceParams] | None = None,
        motion: MotionParams | None = None,
    ) -> None:
        self._robot = robot
        self._geometry = geometry
        self._captures = TrayState(capture_tray)
        self._reserve = TrayState(reserve_tray)
        self._pieces = piece_params or DEFAULT_PIECE_PARAMS
        self._motion = motion or MotionParams()
        self._transit_z = (
            geometry.max_z
            + tallest_piece_height(self._pieces)
            + self._motion.transit_clearance_m
        )

    # ------------------------------------------------------------- primitivas

    def _pick(self, position: Point3, piece_type: chess.PieceType) -> None:
        params = self._pieces[piece_type]
        motion = self._motion
        grip = Pose(Point3(position.x, position.y, position.z + params.grip_height_m))
        approach = grip.at_height(grip.position.z + motion.approach_clearance_m)
        transit = grip.at_height(self._transit_z)

        self._robot.move_linear(transit, motion.speed_travel, motion.acceleration)
        self._robot.gripper_move(params.approach_opening_mm, params.grip_force)
        self._robot.move_linear(approach, motion.speed_vertical, motion.acceleration)
        self._robot.move_linear(grip, motion.speed_vertical, motion.acceleration)
        self._robot.gripper_move(params.grip_opening_mm, params.grip_force)
        self._robot.move_linear(transit, motion.speed_vertical, motion.acceleration)

    def _place(self, position: Point3, piece_type: chess.PieceType) -> None:
        params = self._pieces[piece_type]
        motion = self._motion
        drop = Pose(Point3(position.x, position.y, position.z + params.grip_height_m))
        transit = drop.at_height(self._transit_z)
        retreat = drop.at_height(drop.position.z + motion.approach_clearance_m)

        self._robot.move_linear(transit, motion.speed_travel, motion.acceleration)
        self._robot.move_linear(drop, motion.speed_vertical, motion.acceleration)
        self._robot.gripper_move(params.approach_opening_mm, params.grip_force)
        self._robot.move_linear(retreat, motion.speed_vertical, motion.acceleration)

    def _transfer(
        self, source: Point3, target: Point3, piece_type: chess.PieceType
    ) -> None:
        self._pick(source, piece_type)
        self._place(target, piece_type)

    # -------------------------------------------------------------- jugadas

    def execute_move(self, board: chess.Board, move: chess.Move) -> list[str]:
        """Ejecuta la jugada sobre el tablero físico.

        ``board`` es la posición ANTES de la jugada (para conocer piezas y
        capturas). Devuelve la lista de manipulaciones, para log y UI.
        """
        if move not in board.legal_moves:
            raise ValueError(f"Jugada ilegal: {move.uci()}")

        steps: list[str] = []
        piece = board.piece_at(move.from_square)
        assert piece is not None

        # 1. Retirar la pieza capturada (si la hay) a la bandeja.
        if board.is_en_passant(move):
            captured_square = chess.square(
                chess.square_file(move.to_square), chess.square_rank(move.from_square)
            )
        elif board.is_capture(move):
            captured_square = move.to_square
        else:
            captured_square = None

        if captured_square is not None:
            captured = board.piece_at(captured_square)
            assert captured is not None
            slot = self._captures.next_slot()
            self._transfer(
                self._geometry.square_center(captured_square), slot, captured.piece_type
            )
            steps.append(
                f"captura: {chess.SQUARE_NAMES[captured_square]} → bandeja "
                f"(slot {self._captures.used - 1})"
            )

        source = self._geometry.square_center(move.from_square)
        target = self._geometry.square_center(move.to_square)

        # 2. Mover la pieza propia (o promocionar).
        if move.promotion:
            # Peón a la bandeja de capturas, pieza de promoción desde la reserva.
            slot = self._captures.next_slot()
            self._transfer(source, slot, chess.PAWN)
            steps.append(
                f"promoción: peón {chess.SQUARE_NAMES[move.from_square]} → bandeja"
            )
            reserve = self._reserve.take_slot()
            self._transfer(reserve, target, move.promotion)
            steps.append(
                f"promoción: {chess.piece_name(move.promotion)} de reserva → "
                f"{chess.SQUARE_NAMES[move.to_square]}"
            )
        else:
            self._transfer(source, target, piece.piece_type)
            steps.append(
                f"mover: {chess.SQUARE_NAMES[move.from_square]} → "
                f"{chess.SQUARE_NAMES[move.to_square]}"
            )

        # 3. Enroque: mover también la torre.
        if board.is_castling(move):
            kingside = chess.square_file(move.to_square) == 6
            rook_from = chess.H1 if kingside else chess.A1
            if board.turn == chess.BLACK:
                rook_from += 56
            rook_to = (move.from_square + move.to_square) // 2
            self._transfer(
                self._geometry.square_center(rook_from),
                self._geometry.square_center(rook_to),
                chess.ROOK,
            )
            steps.append(
                f"enroque: torre {chess.SQUARE_NAMES[rook_from]} → "
                f"{chess.SQUARE_NAMES[rook_to]}"
            )

        return steps

    @property
    def captures_used(self) -> int:
        return self._captures.used

    @property
    def reserve_used(self) -> int:
        return self._reserve.used

    def reset_trays(self) -> None:
        """Partida nueva: el operador vació las bandejas y repuso la reserva."""
        self._captures.used = 0
        self._reserve.used = 0
