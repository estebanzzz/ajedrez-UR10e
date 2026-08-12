"""Representación del bitmap de ocupación del tablero sensorizado.

La matriz 8x8 de sensores reed/hall solo reporta presencia. El bitmap usa la
misma convención que los bitboards de python-chess: bit 0 = a1, bit 7 = h1,
bit 56 = a8, bit 63 = h8. Así, ``chess.Board().occupied`` es directamente el
bitmap esperado para una posición dada.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

# Un bitmap es un entero de 64 bits; alias para legibilidad.
Bitmap = int

FULL_START_BITMAP: Bitmap = chess.Board().occupied


def bitmap_from_squares(squares: list[chess.Square] | set[chess.Square]) -> Bitmap:
    bitmap = 0
    for square in squares:
        bitmap |= 1 << square
    return bitmap


def squares_from_bitmap(bitmap: Bitmap) -> list[chess.Square]:
    return list(chess.scan_forward(bitmap))


@dataclass(frozen=True)
class BitmapDiff:
    """Diferencia entre dos bitmaps: casillas que se vaciaron y se ocuparon."""

    vacated: tuple[chess.Square, ...]
    occupied: tuple[chess.Square, ...]

    @property
    def is_empty(self) -> bool:
        return not self.vacated and not self.occupied


def diff_bitmaps(before: Bitmap, after: Bitmap) -> BitmapDiff:
    vacated = tuple(chess.scan_forward(before & ~after))
    occupied = tuple(chess.scan_forward(after & ~before))
    return BitmapDiff(vacated=vacated, occupied=occupied)


def format_bitmap(bitmap: Bitmap) -> str:
    """Dibuja el bitmap como una grilla 8x8 (fila 8 arriba), útil en diagnóstico."""
    rows = []
    for rank in range(7, -1, -1):
        cells = []
        for file in range(8):
            square = chess.square(file, rank)
            cells.append("●" if bitmap & (1 << square) else "·")
        rows.append(f"{rank + 1} " + " ".join(cells))
    rows.append("  a b c d e f g h")
    return "\n".join(rows)
