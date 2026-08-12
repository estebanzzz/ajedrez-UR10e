import chess

from app.board_sensor.bitmap import (
    FULL_START_BITMAP,
    bitmap_from_squares,
    diff_bitmaps,
    format_bitmap,
    squares_from_bitmap,
)


def test_start_bitmap_matches_python_chess():
    assert FULL_START_BITMAP == chess.Board().occupied
    assert bin(FULL_START_BITMAP).count("1") == 32


def test_bitmap_roundtrip():
    squares = [chess.A1, chess.E4, chess.H8]
    bitmap = bitmap_from_squares(squares)
    assert squares_from_bitmap(bitmap) == squares


def test_diff_detects_vacated_and_occupied():
    before = bitmap_from_squares([chess.E2, chess.D7])
    after = bitmap_from_squares([chess.E4, chess.D7])
    diff = diff_bitmaps(before, after)
    assert diff.vacated == (chess.E2,)
    assert diff.occupied == (chess.E4,)
    assert not diff.is_empty


def test_diff_empty_when_equal():
    assert diff_bitmaps(FULL_START_BITMAP, FULL_START_BITMAP).is_empty


def test_format_bitmap_has_8_ranks():
    text = format_bitmap(FULL_START_BITMAP)
    lines = text.splitlines()
    assert len(lines) == 9  # 8 filas + rótulo de columnas
    assert lines[0].startswith("8")
