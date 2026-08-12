"""Orquestador de partida: integra sensores, detector, motor y robot.

Ciclo de juego (Fase 4):

1. Turno humano: el ``SensorDetectorBridge`` sigue los cambios del tablero;
   al pulsar el botón de confirmación se resuelve la jugada.
   - Jugada ilegal → estado ``HUMAN_ERROR`` con las casillas en conflicto;
     el humano restaura y vuelve a intentar.
2. Turno del robot: el motor elige, el ``RobotController`` ejecuta y el
   resultado se **verifica contra los sensores** (``wait_for_bitmap``).
   - Verificación fallida (pieza mal agarrada/caída) → ``RESYNC``.
3. Modo resync: la UI muestra las casillas que difieren; cuando el operador
   restaura la posición esperada, la partida continúa sola.
"""

from __future__ import annotations

import enum
import logging
import threading
from typing import Callable, Protocol

import chess

from app.board_sensor.bitmap import Bitmap
from app.board_sensor.scanner import BoardScanner
from app.game_state.game import GameState
from app.move_detector import DetectionError, DetectionResult, DetectorPhase
from app.move_detector.bridge import SensorDetectorBridge
from app.robot_controller.controller import RobotController

logger = logging.getLogger(__name__)

ROBOT_VERIFY_TIMEOUT_S = 5.0


class MatchPhase(enum.Enum):
    IDLE = "idle"  # sin partida en curso
    HUMAN_TURN = "human_turn"
    HUMAN_ERROR = "human_error"  # confirmó una posición ilegal
    ROBOT_TURN = "robot_turn"
    RESYNC = "resync"  # el tablero físico no coincide; espera al operador
    GAME_OVER = "game_over"


class Engine(Protocol):
    """Contrato mínimo del motor (StockfishEngine o RandomEngine)."""

    def choose_move(self, board: chess.Board) -> chess.Move: ...

    def evaluate(self, board: chess.Board, time_limit: float = 0.3): ...


class GameOrchestrator:
    def __init__(
        self,
        game: GameState,
        scanner: BoardScanner,
        engine: Engine,
        robot: RobotController,
        on_robot_moved: Callable[[Bitmap], None] | None = None,
    ) -> None:
        """``on_robot_moved`` solo se usa en simulación: recibe el bitmap
        esperado tras el movimiento del robot y actualiza el driver mock
        (con hardware real, el mundo físico cambia solo)."""
        self._game = game
        self._scanner = scanner
        self._engine = engine
        self._robot = robot
        self._on_robot_moved = on_robot_moved
        self._bridge = SensorDetectorBridge(scanner, game.board)
        self._lock = threading.RLock()
        self._phase = MatchPhase.IDLE
        self._last_error: str | None = None
        self._robot_steps: list[str] = []
        self._evaluation: dict | None = None

    # ------------------------------------------------------------------ estado

    @property
    def phase(self) -> MatchPhase:
        with self._lock:
            return self._phase

    def status(self) -> dict:
        """Estado completo para la API/UI."""
        with self._lock:
            sensor_bitmap = self._scanner.latest
            mismatched = (
                self._game.mismatched_squares(sensor_bitmap)
                if sensor_bitmap is not None
                else []
            )
            outcome = self._game.outcome()
            move_stack = self._game.board.move_stack
            return {
                "phase": self._phase.value,
                "fen": self._game.fen,
                "last_move": move_stack[-1].uci() if move_stack else None,
                "in_check": self._game.board.is_check(),
                "evaluation": self._evaluation,
                "turn": "white" if self._game.turn == chess.WHITE else "black",
                "human_color": (
                    "white" if self._game.human_color == chess.WHITE else "black"
                ),
                "san_history": self._game.san_history,
                "detector_phase": self._bridge.phase.value,
                "mismatched_squares": [chess.SQUARE_NAMES[s] for s in mismatched],
                "last_error": self._last_error,
                "robot_steps": self._robot_steps,
                "outcome": (
                    {
                        "termination": outcome.termination,
                        "result": outcome.result,
                        "winner": (
                            None
                            if outcome.winner is None
                            else ("white" if outcome.winner == chess.WHITE else "black")
                        ),
                    }
                    if outcome
                    else None
                ),
            }

    # ------------------------------------------------------------------ flujo

    def new_game(self, human_color: chess.Color = chess.WHITE) -> None:
        with self._lock:
            self._bridge.stop()
            self._game.reset()
            self._game.human_color = human_color
            self._robot.reset_trays()
            self._last_error = None
            self._robot_steps = []
            self._evaluation = None
            self._update_evaluation()
            if human_color == chess.WHITE:
                self._enter_human_turn()
            else:
                self._phase = MatchPhase.ROBOT_TURN
        if human_color == chess.BLACK:
            self._robot_turn()

    def confirm(self) -> dict:
        """Botón de confirmación del humano. Devuelve el status resultante."""
        with self._lock:
            if self._phase not in (MatchPhase.HUMAN_TURN, MatchPhase.HUMAN_ERROR):
                self._last_error = "No es el turno del humano."
                return self.status()

            result = self._bridge.confirm()
            if isinstance(result, DetectionError):
                self._last_error = result.message
                self._phase = MatchPhase.HUMAN_ERROR
                # Re-armar el detector para que siga el arreglo del tablero.
                self._bridge.start_turn()
                return self.status()

            assert isinstance(result, DetectionResult)
            self._game.apply_move(result.move)
            self._last_error = None
            self._update_evaluation()

            if self._game.outcome() is not None:
                self._phase = MatchPhase.GAME_OVER
                return self.status()
            self._phase = MatchPhase.ROBOT_TURN

        self._robot_turn()
        return self.status()

    def resync_check(self) -> bool:
        """En RESYNC: si el tablero ya coincide, reanuda. Devuelve si reanudó."""
        with self._lock:
            if self._phase != MatchPhase.RESYNC:
                return False
            sensor_bitmap = self._scanner.latest
            if sensor_bitmap != self._game.expected_bitmap:
                return False
            self._last_error = None
            if self._game.is_human_turn:
                self._enter_human_turn()
                return True
            self._phase = MatchPhase.ROBOT_TURN
        self._robot_turn()
        return True

    # ---------------------------------------------------------------- interno

    def _update_evaluation(self) -> None:
        """Evaluación para la barra de la UI; nunca debe frenar la partida."""
        try:
            evaluation = self._engine.evaluate(self._game.board)
        except Exception:
            logger.exception("Fallo al evaluar la posición")
            return
        self._evaluation = {
            "cp": evaluation.centipawns,
            "mate": evaluation.mate_in,
            "display": str(evaluation),
        }

    def _enter_human_turn(self) -> None:
        self._phase = MatchPhase.HUMAN_TURN
        self._bridge.start_turn()

    def _robot_turn(self) -> None:
        with self._lock:
            if self._phase != MatchPhase.ROBOT_TURN:
                return
            board_before = self._game.board.copy(stack=False)
            move = self._engine.choose_move(board_before)
            logger.info("Robot juega %s", move.uci())
            self._robot_steps = self._robot.execute_move(board_before, move)
            self._game.apply_move(move)
            self._update_evaluation()
            expected = self._game.expected_bitmap
            if self._on_robot_moved is not None:
                self._on_robot_moved(expected)

        # Verificación física fuera del lock (espera al scanner).
        if not self._scanner.wait_for_bitmap(expected, timeout=ROBOT_VERIFY_TIMEOUT_S):
            with self._lock:
                self._last_error = (
                    "El tablero no refleja la jugada del robot "
                    "(¿pieza mal agarrada?). Corregir y continuar."
                )
                self._phase = MatchPhase.RESYNC
            return

        with self._lock:
            if self._game.outcome() is not None:
                self._phase = MatchPhase.GAME_OVER
            else:
                self._enter_human_turn()
