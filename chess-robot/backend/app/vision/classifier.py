"""Clasificación de casillas: vacía / ficha (v4, solo presencia).

Ambos bandos usan fichas OSCURAS sobre el tablero claro (p. ej. azul y
gris): la visión ya no distingue bandos — solo presencia/ausencia — por el
mismo camino de píxeles oscuros que siempre detectó las negras al 100 %.
La identidad y el bando de cada pieza los lleva el seguimiento de estado
(``game_state``), igual que con los drivers de matriz reed.

Una única señal **relativa** — independiente del nivel de iluminación
absoluto — decide todo: la oscuridad de cada píxel respecto del fondo de
su casilla (las 4 esquinas de la celda, siempre visibles porque la ficha
es un disco inscrito). Una ficha hunde ese cociente; si la sala entera se
oscurece, numerador y denominador caen juntos y nada cambia.

El umbral píxel-oscuro no es fijo: el entrenamiento auto-etiquetado
(tablero vacío + posición inicial) lo deja a mitad de camino entre la
ficha más clara y la casilla vacía más oscura observadas — así una ficha
gris media separa igual de limpio que una casi negra, y las sombras vistas
durante el entrenamiento quedan del lado "vacío" del umbral.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import chess
import cv2
import numpy as np

from app.vision import tuning
from app.vision.geometry import BoardGeometry

LABEL_EMPTY = 0
LABEL_PIECE = 1

MODEL_VERSION = 4

# Lado del parche de esquina (fracción de la celda) usado para estimar el
# brillo del fondo de la casilla. Los 4 parches quedan fuera del disco
# inscrito de la ficha.
CORNER_FRAC = 0.15

_MASK_CACHE: dict[tuple[str, int, float], np.ndarray] = {}


def _disc_mask(size: int, radius: float) -> np.ndarray:
    key = ("disc", size, radius)
    mask = _MASK_CACHE.get(key)
    if mask is None:
        yy, xx = np.mgrid[0:size, 0:size]
        c = (size - 1) / 2.0
        mask = ((xx - c) ** 2 + (yy - c) ** 2) <= (radius * size) ** 2
        _MASK_CACHE[key] = mask
    return mask


def corner_brightness(cell_bgr: np.ndarray) -> float:
    """Brillo del fondo de la casilla, medido en las 4 esquinas de la
    celda — visibles aunque haya una ficha (disco inscrito)."""
    size = min(cell_bgr.shape[0], cell_bgr.shape[1])
    cell = cell_bgr[:size, :size]
    p = max(3, int(size * CORNER_FRAC))
    gray = cell if cell.ndim == 2 else cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    gray = gray.astype(np.float32)
    patches = [gray[:p, :p], gray[:p, -p:], gray[-p:, :p], gray[-p:, -p:]]
    return float(np.mean([patch.mean() for patch in patches]))


def cell_features(cell_bgr: np.ndarray) -> np.ndarray:
    """[ratio] de una celda: brillo del disco central / brillo de esquinas
    (≈1 vacía; se hunde con ficha oscura)."""
    size = min(cell_bgr.shape[0], cell_bgr.shape[1])
    cell = cell_bgr[:size, :size]
    gray = cell if cell.ndim == 2 else cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    gray = gray.astype(np.float32)

    background = max(corner_brightness(cell_bgr), 1.0)
    disc = _disc_mask(size, tuning.TUNING.disc_radius)
    ratio = float(gray[disc].mean()) / background
    return np.array([ratio], dtype=np.float32)


N_FEATURES = 1


@dataclass
class VisionModel:
    """Modelo v4: umbral píxel-oscuro aprendido + referencias de fondo."""

    warp_size: int
    corner_refs: np.ndarray  # (64,) brillo de esquinas al entrenar (guardias)
    # Un píxel es "de ficha" si gris < dark_pixel_frac × fondo de su celda.
    dark_pixel_frac: float
    stats: dict

    # Fracción del disco central oscura para declarar la casilla ocupada.
    PIECE_DISC_FILL = 0.45

    def _effective_dark_frac(self) -> float:
        # threshold_scale >1 = más conservador (menos fichas fantasma):
        # baja el umbral píxel-oscuro; <1 = más sensible.
        scale = max(tuning.TUNING.threshold_scale, 1e-6)
        return float(np.clip(self.dark_pixel_frac / scale, 0.05, 0.95))

    def classify_board(
        self, warped: np.ndarray, geometry: BoardGeometry
    ) -> tuple[list[int], list[float]]:
        """Fichas como OBJETOS de píxeles oscuros.

        Una casilla está ocupada si su disco central está mayormente oscuro
        (funciona incluso con filas de fichas que se tocan) o si el
        centroide de una mancha con área y solidez de disco cae en ella
        (ficha descentrada). El fondo se estima POR CELDA con las esquinas,
        así los gradientes de luz a lo largo del tablero no mueven el
        umbral.
        """
        gray = (
            warped if warped.ndim == 2 else cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
        ).astype(np.float32)

        bg_cells = np.array(
            [corner_brightness(geometry.cell(warped, sq)) for sq in chess.SQUARES],
            dtype=np.float32,
        )
        bg_global = max(1.0, float(np.median(bg_cells)))
        # Piso al 50% del fondo global: una ficha alta que cubre las
        # esquinas de su propia celda no debe anular su propio umbral.
        bg_map = np.empty_like(gray)
        for sq in chess.SQUARES:
            x0, y0, x1, y1 = geometry.cell_rect(sq)
            bg_map[y0:y1, x0:x1] = max(float(bg_cells[sq]), 0.5 * bg_global)

        dark = (gray < self._effective_dark_frac() * bg_map).astype(np.uint8)

        cell = geometry.cell_size
        nominal_area = np.pi * (0.37 * cell) ** 2

        fill: dict[chess.Square, float] = {}
        occupied: set[chess.Square] = set()
        for sq in chess.SQUARES:
            x0, y0, x1, y1 = geometry.cell_rect(sq)
            patch = dark[y0:y1, x0:x1]
            disc = _disc_mask(x1 - x0, tuning.TUNING.disc_radius)
            frac = float(patch[disc].mean())
            fill[sq] = frac
            if frac >= self.PIECE_DISC_FILL:
                occupied.add(sq)

        # Fichas descentradas: manchas con área y solidez de disco cuyo
        # centroide cae en la casilla (arcos/sombras chicas no pasan).
        count, _, stats, centroids = cv2.connectedComponentsWithStats(dark, 8)
        for i in range(1, count):
            area = stats[i, cv2.CC_STAT_AREA]
            if area < 0.35 * nominal_area:
                continue  # sombras chicas / ruido
            bbox_area = stats[i, cv2.CC_STAT_WIDTH] * stats[i, cv2.CC_STAT_HEIGHT]
            if area < 0.5 * bbox_area:
                continue  # arco/anillo, no disco macizo
            cx, cy = centroids[i]
            file = int(cx // cell)
            rank = 7 - int(cy // cell)
            if 0 <= file <= 7 and 0 <= rank <= 7:
                occupied.add(chess.square(file, rank))

        labels: list[int] = []
        margins: list[float] = []
        for sq in chess.SQUARES:
            if sq in occupied:
                labels.append(LABEL_PIECE)
                margins.append(max(0.3, fill[sq] - self.PIECE_DISC_FILL + 0.05))
            else:
                labels.append(LABEL_EMPTY)
                margins.append(
                    float(
                        np.clip(
                            (self.PIECE_DISC_FILL - fill[sq]) / self.PIECE_DISC_FILL,
                            0.0,
                            1.0,
                        )
                    )
                )
        return labels, margins

    # ------------------------------------------------------------ persistencia

    def save(self, path: str | Path) -> None:
        data = {
            "version": MODEL_VERSION,
            "warp_size": self.warp_size,
            "corner_refs": self.corner_refs.tolist(),
            "dark_pixel_frac": self.dark_pixel_frac,
            "stats": self.stats,
        }
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "VisionModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != MODEL_VERSION:
            raise ValueError(
                f"{path}: modelo entrenado con una versión anterior del "
                "clasificador — re-entrena con la página /calibracion"
            )
        return cls(
            warp_size=data["warp_size"],
            corner_refs=np.array(data["corner_refs"], dtype=np.float32),
            dark_pixel_frac=float(data["dark_pixel_frac"]),
            stats=data.get("stats", {}),
        )


# En la posición inicial las filas 1-2 y 7-8 tienen ficha; el resto no.
_PIECE_RANKS = (0, 1, 6, 7)


class Trainer:
    """Acumula frames auto-etiquetados y produce un ``VisionModel`` v4.

    Mismo flujo de siempre: tandas del tablero VACÍO y de la POSICIÓN
    INICIAL, en una o más condiciones de luz. Como la señal es relativa,
    el umbral queda entre distribuciones muy separadas y es estable ante
    cambios de iluminación.
    """

    def __init__(self, geometry: BoardGeometry) -> None:
        self._geometry = geometry
        # Por frame: (features (64, 1), brillo de esquinas (64,))
        self._empty_frames: list[tuple[np.ndarray, np.ndarray]] = []
        self._start_frames: list[tuple[np.ndarray, np.ndarray]] = []

    def _extract(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        warped = self._geometry.warp(frame)
        cells = [self._geometry.cell(warped, sq) for sq in chess.SQUARES]
        feats = np.stack([cell_features(c) for c in cells])
        corners = np.array([corner_brightness(c) for c in cells], dtype=np.float32)
        return feats, corners

    def add_empty_frame(self, frame: np.ndarray) -> None:
        self._empty_frames.append(self._extract(frame))

    def add_start_frame(self, frame: np.ndarray) -> None:
        self._start_frames.append(self._extract(frame))

    @property
    def counts(self) -> tuple[int, int]:
        return len(self._empty_frames), len(self._start_frames)

    def train(self) -> VisionModel:
        if not self._empty_frames or not self._start_frames:
            raise ValueError(
                "Faltan capturas: se necesita al menos un frame del tablero "
                "vacío y uno de la posición inicial"
            )

        # Muestras por clase. Vacías: tablero vacío completo + filas 3-6 de
        # la posición inicial (incluyen las sombras junto a las fichas).
        empty_ratios: list[float] = []
        piece_ratios: list[float] = []
        for feats, _ in self._empty_frames:
            empty_ratios.extend(float(feats[sq][0]) for sq in chess.SQUARES)
        for feats, _ in self._start_frames:
            for sq in chess.SQUARES:
                if chess.square_rank(sq) in _PIECE_RANKS:
                    piece_ratios.append(float(feats[sq][0]))
                else:
                    empty_ratios.append(float(feats[sq][0]))

        corner_refs = np.mean(
            [corners for _, corners in self._empty_frames], axis=0
        ).astype(np.float32)

        # Umbral: entre la ficha más clara y la casilla vacía más oscura.
        piece_hi = float(np.percentile(piece_ratios, 99))
        empty_lo = float(np.percentile(empty_ratios, 1))
        separation = empty_lo / max(piece_hi, 1e-6)
        dark_frac = float(np.clip((piece_hi + empty_lo) / 2.0, 0.05, 0.95))

        stats = {
            "empty_frames": len(self._empty_frames),
            "start_frames": len(self._start_frames),
            "piece_ratio_hi": piece_hi,
            "empty_ratio_lo": empty_lo,
            "separation": separation,
            "separated": bool(piece_hi < empty_lo),
        }
        return VisionModel(
            warp_size=self._geometry.warp_size,
            corner_refs=corner_refs,
            dark_pixel_frac=dark_frac,
            stats=stats,
        )
