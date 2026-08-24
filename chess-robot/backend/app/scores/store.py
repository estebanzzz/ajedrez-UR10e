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
from datetime import datetime, timedelta
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
        # Log completo de partidas (todas: humanas, demos, abandonadas y
        # abortadas) para el sistema de supervisión de la feria. La tabla
        # `games` de arriba queda solo para el ranking/premio diario.
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS game_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_ts TEXT NOT NULL,     -- ISO local
                ended_ts TEXT NOT NULL,
                day TEXT NOT NULL,            -- YYYY-MM-DD local
                duration_s INTEGER NOT NULL,
                mode TEXT NOT NULL,           -- human / self_play
                player_name TEXT NOT NULL,    -- '' en demos o anónimo
                human_color TEXT,             -- white/black, NULL en demos
                difficulty TEXT NOT NULL,
                result TEXT,                  -- POV humano: win/draw/loss/abandoned/aborted (NULL en demos)
                chess_result TEXT,            -- 1-0 / 0-1 / 1/2-1/2 (NULL si se abortó)
                termination TEXT NOT NULL,    -- CHECKMATE / ... / RESIGNATION / ABORTED
                moves INTEGER NOT NULL,       -- plies totales
                san TEXT NOT NULL,            -- jugadas SAN separadas por espacio
                final_fen TEXT NOT NULL,
                score INTEGER,                -- NULL si no compite en el ranking
                material INTEGER NOT NULL     -- material capturado al robot
            )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_log_day ON game_log (day)"
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

    # -------------------------------------------- log completo / supervisión

    def log_game(
        self,
        *,
        started_ts: str,
        ended_ts: str,
        day: str,
        duration_s: int,
        mode: str,
        player_name: str,
        human_color: str | None,
        difficulty: str,
        result: str | None,
        chess_result: str | None,
        termination: str,
        moves: int,
        san: str,
        final_fen: str,
        score: int | None,
        material: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO game_log (started_ts, ended_ts, day, duration_s, mode,"
                " player_name, human_color, difficulty, result, chess_result,"
                " termination, moves, san, final_fen, score, material)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    started_ts, ended_ts, day, duration_s, mode,
                    player_name.strip(), human_color, difficulty, result,
                    chess_result, termination, moves, san, final_fen, score,
                    material,
                ),
            )
            self._conn.commit()

    def stats_summary(self, days: int = 7) -> dict:
        """Agregados para los dashboards de supervisión (últimos ``days`` días)."""
        cutoff = (datetime.now() - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
        totals = self._rows(
            "SELECT COUNT(*) AS games,"
            " COALESCE(SUM(mode = 'human'), 0) AS human_games,"
            " COALESCE(SUM(mode = 'self_play'), 0) AS demos,"
            " COUNT(DISTINCT CASE WHEN mode = 'human' AND player_name != ''"
            "       THEN player_name END) AS players,"
            " CAST(COALESCE(ROUND(AVG(duration_s)), 0) AS INTEGER) AS avg_duration_s,"
            " ROUND(COALESCE(AVG(moves), 0), 1) AS avg_moves,"
            " MAX(score) AS top_score"
            " FROM game_log WHERE day >= ?",
            (cutoff,),
        )[0]
        by_result = {
            row["result"] or "demo": row["n"]
            for row in self._rows(
                "SELECT result, COUNT(*) AS n FROM game_log WHERE day >= ?"
                " GROUP BY result",
                (cutoff,),
            )
        }
        by_difficulty = {
            row["difficulty"]: row["n"]
            for row in self._rows(
                "SELECT difficulty, COUNT(*) AS n FROM game_log"
                " WHERE day >= ? AND mode = 'human' GROUP BY difficulty",
                (cutoff,),
            )
        }
        per_day = self._rows(
            "SELECT day, COUNT(*) AS games,"
            " COALESCE(SUM(mode = 'human'), 0) AS human_games,"
            " COUNT(DISTINCT CASE WHEN mode = 'human' AND player_name != ''"
            "       THEN player_name END) AS players,"
            " CAST(COALESCE(ROUND(AVG(duration_s)), 0) AS INTEGER) AS avg_duration_s"
            " FROM game_log WHERE day >= ? GROUP BY day ORDER BY day",
            (cutoff,),
        )
        per_hour = {
            row["hour"]: row["n"]
            for row in self._rows(
                "SELECT strftime('%H', ended_ts) AS hour, COUNT(*) AS n"
                " FROM game_log WHERE day >= ? GROUP BY hour ORDER BY hour",
                (cutoff,),
            )
        }
        return {
            "days": days,
            "since": cutoff,
            "totals": totals,
            "by_result": by_result,
            "by_difficulty": by_difficulty,
            "per_day": per_day,
            "per_hour": per_hour,
        }

    def list_games(
        self,
        limit: int = 50,
        offset: int = 0,
        day: str | None = None,
        mode: str | None = None,
    ) -> dict:
        """Listado paginado (sin SAN, que puede ser largo) para tablas."""
        where: list[str] = []
        params: list = []
        if day:
            where.append("day = ?")
            params.append(day)
        if mode:
            where.append("mode = ?")
            params.append(mode)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._rows(
            f"SELECT COUNT(*) AS n FROM game_log{clause}", tuple(params)
        )[0]["n"]
        rows = self._rows(
            "SELECT id, started_ts, ended_ts, day, duration_s, mode, player_name,"
            " human_color, difficulty, result, chess_result, termination, moves,"
            f" score, material FROM game_log{clause}"
            " ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return {"total": total, "games": rows}

    def game_detail(self, game_id: int) -> dict | None:
        """Partida completa (con SAN y FEN final) o ``None`` si no existe."""
        rows = self._rows("SELECT * FROM game_log WHERE id = ?", (game_id,))
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
