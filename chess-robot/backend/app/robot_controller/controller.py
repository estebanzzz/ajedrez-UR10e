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

import time
from dataclasses import dataclass, field

import chess

from app.robot_controller.geometry import BoardGeometry, Point3, TrayGrid
from app.robot_controller.pieces import (
    DEFAULT_PIECE_PARAMS,
    PieceParams,
    tallest_piece_height,
)
from app.robot_controller.robot import Pose, RobotInterface

# El caballo es demasiado fino a la altura de agarre: con la apertura
# calibrada para el resto de las piezas la garra no llega a sujetarlo. Para
# estas piezas la pinza cierra del todo (0 mm) — el control de fuerza de la
# Hand-E se detiene solo al hacer contacto con la pieza.
FULL_CLOSE_PIECE_TYPES = frozenset({chess.KNIGHT})


@dataclass(frozen=True)
class MotionParams:
    """Velocidades reducidas por defecto: entorno público (spec sección 4)."""

    speed_travel: float = 0.25  # m/s entre casillas a altura de tránsito
    speed_vertical: float = 0.08  # m/s en descensos/ascensos
    acceleration: float = 0.4  # m/s^2
    approach_clearance_m: float = 0.06  # altura de aproximación sobre la pieza
    transit_clearance_m: float = 0.04  # margen sobre la pieza más alta
    # Pausa tras cerrar la garra (asentar el agarre antes de levantar) y
    # tras abrirla (que la pieza apoye antes de retirarse).
    grip_settle_s: float = 0.4
    # Tope de apertura de la garra durante el juego (mm): las aperturas de
    # aproximación por pieza se recortan a este valor.
    max_opening_mm: float = 50.0


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
        park_position: Point3 | None = None,
    ) -> None:
        self._robot = robot
        self._geometry = geometry
        self._captures = TrayState(capture_tray)
        self._reserve = TrayState(reserve_tray)
        self._pieces = piece_params or DEFAULT_PIECE_PARAMS
        self._motion = motion or MotionParams()
        self._park_override = park_position
        self._recompute_derived()

    def _recompute_derived(self) -> None:
        self._transit_z = (
            self._geometry.max_z
            + tallest_piece_height(self._pieces)
            + self._motion.transit_clearance_m
        )
        # Posición de espera (turno humano): sin sombra sobre el tablero ni
        # oclusión de la cámara. Si fue calibrada ("park"), se usa tal cual;
        # si no, se deriva: sobre la bandeja de capturas a altura segura.
        if self._park_override is not None:
            self._park_pose = Pose(self._park_override)
        else:
            tray0 = self._captures.tray.slot_position(0)
            self._park_pose = Pose(Point3(tray0.x, tray0.y, self._transit_z + 0.06))

    @property
    def motion(self) -> MotionParams:
        return self._motion

    def set_motion(self, motion: MotionParams) -> None:
        """Aplica parámetros de movimiento en caliente (página /calibration)."""
        self._motion = motion
        self._recompute_derived()

    @property
    def piece_params(self) -> dict[chess.PieceType, PieceParams]:
        return dict(self._pieces)

    def set_gripper_params(
        self,
        grip_opening_mm: float | None = None,
        grip_force: float | None = None,
    ) -> None:
        """Cierre/fuerza de garra uniformes para todas las piezas (fichas
        iguales), en caliente desde la página /calibration. El cierre no
        aplica a ``FULL_CLOSE_PIECE_TYPES``: esas piezas siempre cierran a
        0 mm (la fuerza sí las afecta)."""
        from dataclasses import replace

        changes = {
            k: v
            for k, v in {
                "grip_opening_mm": grip_opening_mm,
                "grip_force": grip_force,
            }.items()
            if v is not None
        }
        if changes:
            self._pieces = {
                piece: replace(params, **changes)
                for piece, params in self._pieces.items()
            }

    def _opening(self, opening_mm: float) -> float:
        """Recorta la apertura al tope configurado para el juego."""
        return min(opening_mm, self._motion.max_opening_mm)

    def _settle(self) -> None:
        """Pausa de asentamiento tras cerrar/abrir la garra."""
        if self._motion.grip_settle_s > 0:
            time.sleep(self._motion.grip_settle_s)

    # ------------------------------------------------------------- primitivas

    def _pick(self, position: Point3, piece_type: chess.PieceType) -> None:
        params = self._pieces[piece_type]
        motion = self._motion
        grip = Pose(Point3(position.x, position.y, position.z + params.grip_height_m))
        approach = grip.at_height(grip.position.z + motion.approach_clearance_m)
        transit = grip.at_height(self._transit_z)

        closing = (
            0.0
            if piece_type in FULL_CLOSE_PIECE_TYPES
            else self._opening(params.grip_opening_mm)
        )

        self._robot.move_linear(transit, motion.speed_travel, motion.acceleration)
        self._robot.gripper_move(self._opening(params.approach_opening_mm), params.grip_force)
        self._robot.move_linear(approach, motion.speed_vertical, motion.acceleration)
        self._robot.move_linear(grip, motion.speed_vertical, motion.acceleration)
        self._robot.gripper_move(closing, params.grip_force)
        self._settle()  # asentar el agarre antes de levantar
        self._robot.move_linear(transit, motion.speed_vertical, motion.acceleration)

    def _place(self, position: Point3, piece_type: chess.PieceType) -> Pose:
        """Suelta la pieza y se retira un poco. Devuelve la pose de tránsito
        (altura segura) sobre la casilla, por si hay que subir ahí después."""
        params = self._pieces[piece_type]
        motion = self._motion
        drop = Pose(Point3(position.x, position.y, position.z + params.grip_height_m))
        transit = drop.at_height(self._transit_z)
        retreat = drop.at_height(drop.position.z + motion.approach_clearance_m)

        self._robot.move_linear(transit, motion.speed_travel, motion.acceleration)
        self._robot.move_linear(drop, motion.speed_vertical, motion.acceleration)
        self._robot.gripper_move(self._opening(params.approach_opening_mm), params.grip_force)
        self._settle()  # dejar que la pieza apoye antes de retirarse
        self._robot.move_linear(retreat, motion.speed_vertical, motion.acceleration)
        return transit

    def _transfer(
        self, source: Point3, target: Point3, piece_type: chess.PieceType
    ) -> Pose:
        self._pick(source, piece_type)
        return self._place(target, piece_type)

    def park(self) -> None:
        """Lleva el brazo a la posición de espera, fuera del tablero."""
        self._robot.move_linear(
            self._park_pose, self._motion.speed_travel, self._motion.acceleration
        )

    # -------------------------------------------------------------- jugadas

    def execute_move(
        self, board: chess.Board, move: chess.Move, park: bool = True
    ) -> list[str]:
        """Ejecuta la jugada sobre el tablero físico.

        ``board`` es la posición ANTES de la jugada (para conocer piezas y
        capturas). Devuelve la lista de manipulaciones, para log y UI.

        ``park=False`` (demo robot vs robot): en vez de retirarse a la
        posición de espera, el brazo solo sube en vertical a la altura de
        tránsito sobre la última casilla, listo para la siguiente jugada.
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

        last_transit: Pose | None = None
        if captured_square is not None:
            captured = board.piece_at(captured_square)
            assert captured is not None
            slot = self._captures.next_slot()
            last_transit = self._transfer(
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
            last_transit = self._transfer(reserve, target, move.promotion)
            steps.append(
                f"promoción: {chess.piece_name(move.promotion)} de reserva → "
                f"{chess.SQUARE_NAMES[move.to_square]}"
            )
        else:
            last_transit = self._transfer(source, target, piece.piece_type)
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
            last_transit = self._transfer(
                self._geometry.square_center(rook_from),
                self._geometry.square_center(rook_to),
                chess.ROOK,
            )
            steps.append(
                f"enroque: torre {chess.SQUARE_NAMES[rook_from]} → "
                f"{chess.SQUARE_NAMES[rook_to]}"
            )

        # 4. Retirarse a la posición de espera: deja la vista de la cámara
        #    despejada para la verificación y el turno humano. En la demo
        #    alcanza con subir a altura segura (ahorra dos traslados por jugada).
        if park:
            self.park()
            steps.append("espera: brazo fuera del tablero")
        else:
            assert last_transit is not None
            self._robot.move_linear(
                last_transit, self._motion.speed_vertical, self._motion.acceleration
            )
            steps.append("tránsito: brazo en altura segura")

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
