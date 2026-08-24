"""Dibujado de overlays para las herramientas interactivas de visión."""

from __future__ import annotations

import chess
import cv2
import numpy as np

from app.vision.classifier import LABEL_PIECE
from app.vision.geometry import BoardGeometry

_GREEN = (80, 220, 80)
_BLUE = (255, 160, 40)
_RED = (60, 60, 230)


def draw_grid(warped: np.ndarray, geometry: BoardGeometry) -> np.ndarray:
    out = warped.copy()
    c = geometry.cell_size
    for i in range(9):
        cv2.line(out, (i * c, 0), (i * c, geometry.warp_size), _GREEN, 1)
        cv2.line(out, (0, i * c), (geometry.warp_size, i * c), _GREEN, 1)
    for square in (chess.A1, chess.H1, chess.A8, chess.H8):
        x, y = geometry.cell_center(square)
        cv2.putText(
            out,
            chess.square_name(square),
            (x - 10, y + 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            _RED,
            1,
            cv2.LINE_AA,
        )
    return out


def draw_labels(
    warped: np.ndarray,
    geometry: BoardGeometry,
    labels: list[int],
    margins: list[float] | None = None,
    low_margin: float = 0.25,  # margen adimensional del clasificador v4
) -> np.ndarray:
    """Marca cada casilla: círculo magenta = ficha, punto azul = vacía.
    Colores saturados a propósito: sobre la imagen (gris en cámaras
    monocromas) un círculo blanco o negro sería invisible.

    Las casillas con margen de decisión bajo se rodean en rojo (lectura poco
    confiable — revisar iluminación o re-entrenar).
    """
    out = draw_grid(warped, geometry)
    r = geometry.cell_size // 3
    for square in chess.SQUARES:
        x, y = geometry.cell_center(square)
        label = labels[square]
        if label == LABEL_PIECE:
            cv2.circle(out, (x, y), r, (255, 0, 255), 2, cv2.LINE_AA)
        else:
            cv2.circle(out, (x, y), 2, _BLUE, -1, cv2.LINE_AA)
        if margins is not None and margins[square] < low_margin:
            cv2.circle(out, (x, y), r + 4, _RED, 2, cv2.LINE_AA)
    return out


def put_help(frame: np.ndarray, lines: list[str]) -> np.ndarray:
    out = frame.copy()
    y = 24
    for line in lines:
        cv2.putText(
            out, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA
        )
        cv2.putText(
            out, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA
        )
        y += 26
    return out
