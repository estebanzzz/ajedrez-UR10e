"""Geometría del tablero en imagen: homografía de 4 esquinas a vista cenital.

El operador marca las 4 esquinas exteriores del área de juego (los vértices
del cuadrado 8x8, no del marco decorativo) en el orden **a1, h1, h8, a8**.
Con ellas se calcula la transformación de perspectiva que rectifica cada
frame a una imagen cenital cuadrada de ``warp_size`` píxeles, donde cada
casilla ocupa una celda de ``warp_size/8``.

Convención de orientación: en la imagen rectificada a1 queda abajo a la
izquierda (fila 1 abajo), igual que un tablero visto por las blancas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import chess
import cv2
import numpy as np

CORNER_NAMES = ("a1", "h1", "h8", "a8")


@dataclass
class BoardGeometry:
    """Homografía imagen → vista cenital y particionado en 64 celdas."""

    corners: list[list[float]]  # 4 puntos [x, y] en píxeles, orden a1, h1, h8, a8
    warp_size: int = 512
    _homography: np.ndarray = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.corners) != 4:
            raise ValueError("Se requieren exactamente 4 esquinas (a1, h1, h8, a8)")
        src = np.array(self.corners, dtype=np.float32)
        s = float(self.warp_size)
        # a1 → abajo-izq, h1 → abajo-der, h8 → arriba-der, a8 → arriba-izq
        dst = np.array([[0, s], [s, s], [s, 0], [0, 0]], dtype=np.float32)
        self._homography = cv2.getPerspectiveTransform(src, dst)

    @property
    def cell_size(self) -> int:
        return self.warp_size // 8

    def warp(self, frame: np.ndarray) -> np.ndarray:
        """Rectifica un frame de cámara a la vista cenital cuadrada."""
        return cv2.warpPerspective(
            frame, self._homography, (self.warp_size, self.warp_size)
        )

    def cell_rect(self, square: chess.Square) -> tuple[int, int, int, int]:
        """Rectángulo (x0, y0, x1, y1) de la casilla en la imagen rectificada."""
        c = self.cell_size
        x0 = chess.square_file(square) * c
        y0 = (7 - chess.square_rank(square)) * c
        return x0, y0, x0 + c, y0 + c

    def cell(self, warped: np.ndarray, square: chess.Square) -> np.ndarray:
        x0, y0, x1, y1 = self.cell_rect(square)
        return warped[y0:y1, x0:x1]

    def cell_center(self, square: chess.Square) -> tuple[int, int]:
        x0, y0, x1, y1 = self.cell_rect(square)
        return (x0 + x1) // 2, (y0 + y1) // 2

    # ------------------------------------------------------------ persistencia

    def save(self, path: str | Path) -> None:
        data = {"version": 1, "corners": self.corners, "warp_size": self.warp_size}
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "BoardGeometry":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(corners=data["corners"], warp_size=data["warp_size"])
