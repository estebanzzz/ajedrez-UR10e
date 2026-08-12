"""Geometría del espacio de trabajo: tablero y bandejas.

La calibración enseña las posiciones TCP de los centros de las 4 casillas de
esquina (a1, h1, a8, h8). Las 64 posiciones se obtienen por interpolación
bilineal, lo que absorbe una leve inclinación o rotación del tablero respecto
a la base del robot. Coordenadas en metros, en la base del UR.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class Point3:
    x: float
    y: float
    z: float

    def __add__(self, other: "Point3") -> "Point3":
        return Point3(self.x + other.x, self.y + other.y, self.z + other.z)

    def scaled(self, factor: float) -> "Point3":
        return Point3(self.x * factor, self.y * factor, self.z * factor)


def _bilinear(a1: Point3, h1: Point3, a8: Point3, h8: Point3, u: float, v: float) -> Point3:
    """u: 0=columna a → 1=columna h; v: 0=fila 1 → 1=fila 8."""
    return (
        a1.scaled((1 - u) * (1 - v))
        + h1.scaled(u * (1 - v))
        + a8.scaled((1 - u) * v)
        + h8.scaled(u * v)
    )


@dataclass(frozen=True)
class BoardGeometry:
    """Centros de las 4 casillas de esquina, enseñados en calibración."""

    a1: Point3
    h1: Point3
    a8: Point3
    h8: Point3

    def square_center(self, square: chess.Square) -> Point3:
        u = chess.square_file(square) / 7.0
        v = chess.square_rank(square) / 7.0
        return _bilinear(self.a1, self.h1, self.a8, self.h8, u, v)

    @property
    def max_z(self) -> float:
        """Cota superior del plano del tablero (para la altura de tránsito)."""
        return max(self.a1.z, self.h1.z, self.a8.z, self.h8.z)


@dataclass(frozen=True)
class TrayGrid:
    """Bandeja con posiciones en grilla (capturas o reserva de promoción).

    ``origin`` es el centro del slot 0; los slots avanzan primero a lo largo
    de ``col_step`` (columnas) y luego de ``row_step`` (filas).
    """

    origin: Point3
    col_step: Point3
    row_step: Point3
    cols: int
    rows: int

    @property
    def capacity(self) -> int:
        return self.cols * self.rows

    def slot_position(self, index: int) -> Point3:
        if not 0 <= index < self.capacity:
            raise IndexError(f"Slot {index} fuera de rango (capacidad {self.capacity})")
        row, col = divmod(index, self.cols)
        return self.origin + self.col_step.scaled(col) + self.row_step.scaled(row)
