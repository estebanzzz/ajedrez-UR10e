"""Puntajes y ranking de la feria.

Cada partida terminada se registra con nombre y puntaje en SQLite (persiste
reinicios de la Pi). El ranking del día alimenta el premio diario.

Fórmula del puntaje (pensada para que perder bien también sume — contra
Stockfish casi todos pierden):

    base por resultado: victoria 1000 / tablas 400 / derrota 0
  + 4 puntos por cada jugada propia (resistencia)
  + 15 puntos por cada punto de material capturado al robot (peón=1 … dama=9)
  × multiplicador de dificultad (principiante ×1, intermedio ×1.5,
    avanzado ×2, máximo ×3)

Ajustar los pesos acá si el balance del premio no convence en la práctica.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

RESULT_BASE = {"win": 1000, "draw": 400, "loss": 0}
POINTS_PER_MOVE = 4
POINTS_PER_MATERIAL = 15
DIFFICULTY_MULTIPLIER = {
    "principiante": 1.0,
    "intermedio": 1.5,
    "avanzado": 2.0,
    "maximo": 3.0,
}


def compute_score(
    result: str, human_moves: int, material_captured: int, difficulty: str
) -> int:
    if result not in RESULT_BASE:
        raise ValueError(f"Resultado desconocido: {result!r}")
    raw = (
        RESULT_BASE[result]
        + POINTS_PER_MOVE * human_moves
        + POINTS_PER_MATERIAL * material_captured
    )
    return round(raw * DIFFICULTY_MULTIPLIER.get(difficulty, 1.0))


class ScoreStore:
    """Registro de partidas en SQLite (thread-safe)."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,             -- ISO local
                day TEXT NOT NULL,            -- YYYY-MM-DD local (premio diario)
                name TEXT NOT NULL,
                score INTEGER NOT NULL,
                result TEXT NOT NULL,         -- win / draw / loss
                difficulty TEXT NOT NULL,
                moves INTEGER NOT NULL,
                material INTEGER NOT NULL
            )"""
        )
        self._conn.commit()

    def record(
        self,
        name: str,
        score: int,
        result: str,
        difficulty: str,
        moves: int,
        material: int,
        when: datetime | None = None,
    ) -> None:
        when = when or datetime.now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO games (ts, day, name, score, result, difficulty, moves, material)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    when.isoformat(timespec="seconds"),
                    when.strftime("%Y-%m-%d"),
                    name.strip(),
                    score,
                    result,
                    difficulty,
                    moves,
                    material,
                ),
            )
            self._conn.commit()

    def _rows(self, query: str, params: tuple) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(query, params)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def top_today(self, limit: int = 10, today: str | None = None) -> list[dict]:
        today = today or datetime.now().strftime("%Y-%m-%d")
        return self._rows(
            "SELECT name, score, result, difficulty, moves, ts FROM games"
            " WHERE day = ? ORDER BY score DESC, id ASC LIMIT ?",
            (today, limit),
        )

    def top_alltime(self, limit: int = 10) -> list[dict]:
        return self._rows(
            "SELECT name, score, result, difficulty, moves, ts, day FROM games"
            " ORDER BY score DESC, id ASC LIMIT ?",
            (limit,),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
