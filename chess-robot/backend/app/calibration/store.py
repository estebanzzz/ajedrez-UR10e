"""Persistencia de la calibración (JSON).

La rutina asistida (teach de esquinas y bandejas por freedrive/jog) escribe
esta estructura; el ``RobotController`` la consume al arrancar. El teach en sí
requiere el robot real y se completa en la puesta a punto (Fase 3/4).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import chess

from app.robot_controller.controller import MotionParams
from app.robot_controller.geometry import BoardGeometry, Point3, TrayGrid
from app.robot_controller.pieces import DEFAULT_PIECE_PARAMS, PieceParams

_PIECE_NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
_NAMES_PIECE = {name: piece for piece, name in _PIECE_NAMES.items()}


@dataclass
class CalibrationData:
    board: BoardGeometry
    capture_tray: TrayGrid
    reserve_tray: TrayGrid
    piece_params: dict[chess.PieceType, PieceParams] = field(
        default_factory=lambda: dict(DEFAULT_PIECE_PARAMS)
    )
    motion: MotionParams = field(default_factory=MotionParams)
    robot_host: str = "192.168.1.10"

    def to_dict(self) -> dict:
        return {
            "robot_host": self.robot_host,
            "board": {
                corner: asdict(getattr(self.board, corner))
                for corner in ("a1", "h1", "a8", "h8")
            },
            "capture_tray": asdict(self.capture_tray),
            "reserve_tray": asdict(self.reserve_tray),
            "piece_params": {
                _PIECE_NAMES[piece]: asdict(params)
                for piece, params in self.piece_params.items()
            },
            "motion": asdict(self.motion),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationData":
        def point(d: dict) -> Point3:
            return Point3(**d)

        def tray(d: dict) -> TrayGrid:
            return TrayGrid(
                origin=point(d["origin"]),
                col_step=point(d["col_step"]),
                row_step=point(d["row_step"]),
                cols=d["cols"],
                rows=d["rows"],
            )

        return cls(
            board=BoardGeometry(**{k: point(v) for k, v in data["board"].items()}),
            capture_tray=tray(data["capture_tray"]),
            reserve_tray=tray(data["reserve_tray"]),
            piece_params={
                _NAMES_PIECE[name]: PieceParams(**params)
                for name, params in data["piece_params"].items()
            },
            motion=MotionParams(**data["motion"]),
            robot_host=data.get("robot_host", "192.168.1.10"),
        )


class CalibrationStore:
    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> CalibrationData:
        with self._path.open(encoding="utf-8") as fh:
            return CalibrationData.from_dict(json.load(fh))

    def save(self, data: CalibrationData) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(data.to_dict(), fh, indent=2, sort_keys=True)
