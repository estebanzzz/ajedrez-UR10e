"""Wrapper UCI de Stockfish con dificultad ajustable y evaluación para la UI.

La dificultad se controla con ``UCI_LimitStrength`` + ``UCI_Elo`` (el rango
soportado por Stockfish moderno arranca en ~1320). El nivel máximo desactiva
la limitación. ``RandomEngine`` es un sustituto para desarrollo sin binario.
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass

import chess
import chess.engine


@dataclass(frozen=True)
class Difficulty:
    name: str
    elo: int | None  # None = fuerza máxima (sin UCI_LimitStrength)
    think_time: float  # segundos por jugada


DIFFICULTY_PRESETS: dict[str, Difficulty] = {
    "principiante": Difficulty("principiante", elo=1350, think_time=0.5),
    "intermedio": Difficulty("intermedio", elo=1700, think_time=1.0),
    "avanzado": Difficulty("avanzado", elo=2200, think_time=1.5),
    "maximo": Difficulty("maximo", elo=None, think_time=2.0),
}


@dataclass(frozen=True)
class Evaluation:
    """Evaluación desde el punto de vista de las blancas, para la barra de la UI."""

    centipawns: int | None  # None si hay mate forzado
    mate_in: int | None  # jugadas hasta el mate (negativo = mate contra blancas)

    def __str__(self) -> str:
        if self.mate_in is not None:
            return f"M{self.mate_in}"
        assert self.centipawns is not None
        return f"{self.centipawns / 100:+.2f}"


def find_stockfish() -> str | None:
    """Busca el binario de Stockfish en el PATH."""
    return shutil.which("stockfish")


class StockfishEngine:
    """Motor Stockfish vía UCI. Usar como context manager o llamar a close()."""

    def __init__(self, binary_path: str, difficulty: str = "intermedio") -> None:
        self._engine = chess.engine.SimpleEngine.popen_uci(binary_path)
        self._difficulty = DIFFICULTY_PRESETS[difficulty]
        self._apply_difficulty()

    def _apply_difficulty(self) -> None:
        preset = self._difficulty
        if preset.elo is None:
            self._engine.configure({"UCI_LimitStrength": False})
        else:
            self._engine.configure({"UCI_LimitStrength": True, "UCI_Elo": preset.elo})

    @property
    def difficulty(self) -> str:
        return self._difficulty.name

    def set_difficulty(self, name: str) -> None:
        if name not in DIFFICULTY_PRESETS:
            raise ValueError(f"Dificultad desconocida: {name!r}")
        self._difficulty = DIFFICULTY_PRESETS[name]
        self._apply_difficulty()

    def choose_move(self, board: chess.Board) -> chess.Move:
        result = self._engine.play(
            board, chess.engine.Limit(time=self._difficulty.think_time)
        )
        if result.move is None:
            raise RuntimeError("El motor no devolvió jugada.")
        return result.move

    def evaluate(self, board: chess.Board, time_limit: float = 0.3) -> Evaluation:
        info = self._engine.analyse(board, chess.engine.Limit(time=time_limit))
        score = info["score"].white()
        if score.is_mate():
            return Evaluation(centipawns=None, mate_in=score.mate())
        return Evaluation(centipawns=score.score(), mate_in=None)

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> "StockfishEngine":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class RandomEngine:
    """Motor de respaldo para desarrollo sin Stockfish: juega al azar."""

    difficulty = "aleatorio"

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def set_difficulty(self, name: str) -> None:  # compatibilidad de interfaz
        pass

    def choose_move(self, board: chess.Board) -> chess.Move:
        return self._rng.choice(list(board.legal_moves))

    def evaluate(self, board: chess.Board, time_limit: float = 0.0) -> Evaluation:
        return Evaluation(centipawns=0, mate_in=None)

    def close(self) -> None:
        pass

    def __enter__(self) -> "RandomEngine":
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass
