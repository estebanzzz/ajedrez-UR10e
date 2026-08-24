"""Orquestador de partida: integra sensores, detector, motor y robot.

Ciclo de juego (Fase 4):

1. Turno humano: el ``SensorDetectorBridge`` sigue los cambios del tablero;
   al pulsar el botón de confirmación se resuelve la jugada.
   - Jugada ilegal → estado ``HUMAN_ERROR`` con las casillas en conflicto;
     el humano restaura y vuelve a intentar.
2. Turno del robot: el motor elige, el ``RobotController`` ejecuta y el
   resultado se **verifica contra los sensores** (``wait_for_bitmap``).
   - Verificación fallida (pieza mal agarrada/caída) → ``RESYNC``.
3. Modo resync: la UI muestra las casillas que difieren; en cuanto el
   tablero vuelve a coincidir con la posición esperada, la partida se
   reanuda **sola** (suscriptor del scanner) — el botón "Verificar resync"
   del panel de operador queda como respaldo manual.

Modo demo (``self_play=True``): el robot juega contra sí mismo. No hay turno
humano: tras cada jugada se programa la siguiente en un hilo aparte (con una
pausa ``CHESS_ROBOT_SELF_PLAY_DELAY``) hasta que la partida termina o el
operador la detiene con ``stop_game``. Para que sea dinámica, la cámara solo
se usa **al arrancar** (exige las 32 piezas en la posición inicial; si no,
``RESYNC`` hasta que estén) — entre jugadas no se verifica el tablero ni el
brazo vuelve a la posición de espera. Si el robot no puede ejecutar una
jugada se pasa a ``RESYNC`` para que el operador la coloque a mano. La demo
no entra al ranking.
"""

from __future__ import annotations

import enum
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Protocol

import chess

from app.board_sensor.bitmap import Bitmap
from app.board_sensor.scanner import BoardScanner
from app.game_state.game import GameState
from app.move_detector import (
    DetectionAmbiguity,
    DetectionError,
    DetectionResult,
    DetectorPhase,
)
from app.move_detector.bridge import SensorDetectorBridge
from app.personality.commentator import Commentator, Event
from app.robot_controller.controller import RobotController
from app.scores import ScoreStore, compute_score

_PIECE_VALUES = {1: 1, 2: 3, 3: 3, 4: 5, 5: 9}  # peón..dama (rey no cuenta)

logger = logging.getLogger(__name__)

# Tiempo para que el tablero físico refleje la jugada del robot. Con el
# UR10e real alcanza poco; mientras un humano ejecute las jugadas del robot
# a mano conviene subirlo (CHESS_ROBOT_VERIFY_TIMEOUT). Si expira, se pasa a
# RESYNC, que ahora se auto-recupera al coincidir el tablero.
ROBOT_VERIFY_TIMEOUT_S = float(os.environ.get("CHESS_ROBOT_VERIFY_TIMEOUT", "10.0"))

# Pausa entre jugadas cuando el robot juega contra sí mismo, para que el
# público pueda seguir la partida (y el brazo no encadene movimientos sin
# respiro).
SELF_PLAY_DELAY_S = float(os.environ.get("CHESS_ROBOT_SELF_PLAY_DELAY", "1.5"))


class MatchPhase(enum.Enum):
    IDLE = "idle"  # sin partida en curso
    HUMAN_TURN = "human_turn"
    HUMAN_ERROR = "human_error"  # confirmó una posición ilegal
    # Dos capturas dejan el mismo bitmap y no se vio el estado intermedio:
    # la jugada está hecha, falta que el humano elija cuál fue en la UI.
    HUMAN_CHOICE = "human_choice"
    ROBOT_TURN = "robot_turn"
    RESYNC = "resync"  # el tablero físico no coincide; espera al operador
    # Pausa de emergencia (botón de la pantalla): reloj detenido y jugadas
    # bloqueadas hasta reanudar; para dirimir dudas sobre una jugada.
    PAUSED = "paused"
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
        scores: ScoreStore | None = None,
        commentator: Commentator | None = None,
    ) -> None:
        """``on_robot_moved`` solo se usa en simulación: recibe el bitmap
        esperado tras el movimiento del robot y actualiza el driver mock
        (con hardware real, el mundo físico cambia solo). ``commentator``
        recibe los eventos de la partida para la voz del robot (opcional)."""
        self._game = game
        self._scanner = scanner
        self._engine = engine
        self._robot = robot
        self._on_robot_moved = on_robot_moved
        self._scores = scores
        self._commentator = commentator
        self._bridge = SensorDetectorBridge(scanner, game.board)
        self._lock = threading.RLock()
        self._phase = MatchPhase.IDLE
        self._last_error: str | None = None
        # Candidatas de una captura ambigua, a la espera de que el humano
        # elija en la UI (solo en HUMAN_CHOICE).
        self._pending_choices: list[chess.Move] = []
        # Fase a restaurar al salir de la pausa de emergencia (solo en PAUSED).
        self._paused_phase: MatchPhase | None = None
        self._robot_steps: list[str] = []
        self._evaluation: dict | None = None
        self._player_name = ""
        self._last_game: dict | None = None  # puntaje de la última partida
        # Fin forzado sin outcome en el tablero: "RESIGNATION" (abandonó) o
        # "TIMEOUT" (se le acabó el reloj). None mientras la partida sigue.
        self._loss_reason: str | None = None
        self._started_at: datetime | None = None  # inicio de la partida en curso
        # Reloj de partida: corre SOLO en el turno del humano (HUMAN_TURN y
        # HUMAN_ERROR). None = sin reloj (demo o partida sin límite).
        self._clock_limit_s: float | None = None
        self._clock_used_s = 0.0
        self._clock_run_since: float | None = None  # time.monotonic() o None
        self._self_play = False  # demo: el robot juega ambos bandos
        # Identificador de la partida en curso: los hilos diferidos (pausa
        # entre jugadas de la demo, verificación física) lo comparan para no
        # pisar una partida nueva o detenida.
        self._game_id = 0
        self._cancel = threading.Event()  # corta la pausa de la demo
        # Auto-recuperación del resync: al estabilizarse un bitmap que
        # coincide con lo esperado, la partida se reanuda sin operador.
        scanner.subscribe(self._auto_resync)

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
            if outcome is not None:
                outcome_dict = {
                    "termination": outcome.termination,
                    "result": outcome.result,
                    "winner": (
                        None
                        if outcome.winner is None
                        else ("white" if outcome.winner == chess.WHITE else "black")
                    ),
                }
            elif self._loss_reason:
                # Abandono o tiempo agotado: gana el robot sin outcome en el
                # tablero.
                robot_white = self._game.human_color == chess.BLACK
                outcome_dict = {
                    "termination": self._loss_reason,
                    "result": "1-0" if robot_white else "0-1",
                    "winner": "white" if robot_white else "black",
                }
            else:
                outcome_dict = None
            clock_remaining = self._clock_remaining_s()
            move_stack = self._game.board.move_stack
            return {
                "phase": self._phase.value,
                "mode": "self_play" if self._self_play else "human",
                "player_name": self._player_name,
                "last_game": self._last_game,
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
                # Captura ambigua esperando elección (HUMAN_CHOICE): las
                # jugadas aún no están aplicadas, el SAN sale del tablero actual.
                "pending_choices": [
                    {"uci": move.uci(), "san": self._game.board.san(move)}
                    for move in self._pending_choices
                ],
                "robot_steps": self._robot_steps,
                "speech": self._commentator.status() if self._commentator else None,
                "outcome": outcome_dict,
                "clock": (
                    {
                        "limit_s": int(self._clock_limit_s or 0),
                        "remaining_s": int(-(-clock_remaining // 1)),  # techo
                        "running": self._clock_run_since is not None,
                    }
                    if clock_remaining is not None
                    else None
                ),
            }

    # ------------------------------------------------------------------ flujo

    def new_game(
        self,
        human_color: chess.Color = chess.WHITE,
        player_name: str = "",
        self_play: bool = False,
        time_limit_s: float | None = None,
    ) -> None:
        """Arranca una partida. Con ``self_play`` el robot juega ambos bandos
        (``human_color`` se ignora) y la secuencia corre en un hilo aparte,
        así la llamada vuelve enseguida."""
        with self._lock:
            self._game_id += 1
            game_id = self._game_id
            self._cancel.set()
            self._cancel = threading.Event()
            self._self_play = self_play
            self._bridge.stop()
            self._game.reset()
            self._game.human_color = human_color
            self._game.claim_draw = self_play
            self._robot.reset_trays()
            try:
                # Arrancar con el brazo en espera: vista de cámara despejada.
                self._robot.park()
            except Exception:
                logger.warning("No se pudo ir a la posición de espera", exc_info=True)
            self._last_error = None
            self._pending_choices = []
            self._paused_phase = None
            self._robot_steps = []
            self._evaluation = None
            self._player_name = player_name.strip()
            self._last_game = None
            self._loss_reason = None
            self._started_at = datetime.now()
            # El reloj solo aplica contra un humano; arranca al entrar a su turno.
            self._clock_limit_s = None if self_play else time_limit_s
            self._clock_used_s = 0.0
            self._clock_run_since = None
            self._update_evaluation()
            start_demo = False
            if self_play:
                # Única verificación por cámara de la demo: las 32 piezas en
                # su lugar. Si falta algo, RESYNC muestra las casillas y la
                # demo arranca sola cuando el tablero coincide (auto-resync).
                if self._scanner.latest == self._game.expected_bitmap:
                    self._phase = MatchPhase.ROBOT_TURN
                    start_demo = True
                else:
                    self._phase = MatchPhase.RESYNC
                    self._last_error = (
                        "Colocá las 32 piezas en la posición inicial para "
                        "arrancar la demo."
                    )
            elif human_color == chess.BLACK:
                self._phase = MatchPhase.ROBOT_TURN
            else:
                self._enter_human_turn()
        if self._commentator is not None:
            self._commentator.new_game()
            self._say(Event.SELF_PLAY if self_play else Event.GAME_START)
        if self._clock_limit_s is not None:
            self._start_clock_watcher(game_id)
        if start_demo:
            self._schedule_robot_move(game_id, delay=0.0)
        elif not self_play and human_color == chess.BLACK:
            self._robot_turn()

    def stop_game(self) -> dict:
        """Aborta la partida en curso y vuelve a ``IDLE`` (fin de la demo
        robot vs robot, o reinicio del operador). Si el robot está a mitad
        de una jugada, espera a que la termine. Devuelve el status."""
        with self._lock:
            # Partida cortada a mitad de camino: al log de supervisión igual
            # (cuántas se abandonan también es un dato de la feria).
            if (
                self._phase not in (MatchPhase.IDLE, MatchPhase.GAME_OVER)
                and self._game.board.move_stack
            ):
                self._log_game(
                    result=None if self._self_play else "aborted",
                    chess_result=None,
                    termination="ABORTED",
                    score=None,
                )
            self._game_id += 1
            self._cancel.set()
            self._bridge.stop()
            abandoned = (
                self._phase not in (MatchPhase.IDLE, MatchPhase.GAME_OVER)
                and not self._self_play
            )
            self._self_play = False
            self._phase = MatchPhase.IDLE
            self._last_error = None
            self._pending_choices = []
            self._paused_phase = None
            self._robot_steps = []
            self._loss_reason = None
            self._clock_pause()
            self._clock_limit_s = None
            try:
                self._robot.park()
            except Exception:
                logger.warning("No se pudo ir a la posición de espera", exc_info=True)
        if abandoned:
            self._say(Event.GAME_STOP)
        return self.status()

    def resign(self) -> dict:
        """El humano abandona: la partida termina como derrota, con puntaje.

        Pensado para el botón "Abandonar" de la pantalla pública. La demo
        robot vs robot se corta con ``stop_game`` (no hay quien abandone)."""
        with self._lock:
            if self._self_play or self._phase in (MatchPhase.IDLE, MatchPhase.GAME_OVER):
                self._last_error = "No hay partida que abandonar."
                return self.status()
            # Invalida la verificación/jugada del robot que esté en vuelo.
            self._game_id += 1
            self._cancel.set()
            self._bridge.stop()
            self._clock_pause()
            self._loss_reason = "RESIGNATION"
            self._phase = MatchPhase.GAME_OVER
            self._last_error = None
            self._pending_choices = []
            self._paused_phase = None
            self._record_game(result="loss")
            try:
                self._robot.park()
            except Exception:
                logger.warning("No se pudo ir a la posición de espera", exc_info=True)
        self._say(Event.GAME_STOP)
        return self.status()

    def pause(self) -> dict:
        """Pausa de emergencia (duda con una jugada): detiene el reloj y
        bloquea confirmaciones hasta ``resume``. Solo durante las fases del
        humano — con el brazo en movimiento no hay pausa segura desde acá."""
        with self._lock:
            if self._phase not in (
                MatchPhase.HUMAN_TURN,
                MatchPhase.HUMAN_ERROR,
                MatchPhase.HUMAN_CHOICE,
            ):
                self._last_error = "Solo se puede pausar durante el turno del humano."
                return self.status()
            logger.info("Partida en pausa (fase %s)", self._phase.value)
            self._paused_phase = self._phase
            self._phase = MatchPhase.PAUSED
            self._clock_pause()
        return self.status()

    def resume(self) -> dict:
        """Reanuda la partida tras la pausa de emergencia."""
        with self._lock:
            if self._phase != MatchPhase.PAUSED or self._paused_phase is None:
                self._last_error = "La partida no está en pausa."
                return self.status()
            self._phase = self._paused_phase
            self._paused_phase = None
            logger.info("Partida reanudada (fase %s)", self._phase.value)
            # El detector siguió armado durante la pausa: solo vuelve el
            # reloj (en HUMAN_CHOICE queda pausado, igual que antes).
            if self._phase in (MatchPhase.HUMAN_TURN, MatchPhase.HUMAN_ERROR):
                self._clock_resume()
        return self.status()

    def confirm(self) -> dict:
        """Botón de confirmación del humano. Devuelve el status resultante."""
        with self._lock:
            if self._phase not in (MatchPhase.HUMAN_TURN, MatchPhase.HUMAN_ERROR):
                self._last_error = "No es el turno del humano."
                return self.status()

            result = self._bridge.confirm()
            if isinstance(result, DetectionError):
                logger.warning(
                    "Jugada rechazada: %s (casillas en conflicto: %s)",
                    result.message,
                    ", ".join(chess.SQUARE_NAMES[s] for s in result.mismatched_squares),
                )
                self._last_error = result.message
                self._phase = MatchPhase.HUMAN_ERROR
                # Re-armar el detector para que siga el arreglo del tablero.
                self._bridge.start_turn()
                self._say(Event.ILLEGAL_MOVE)
                return self.status()

            if isinstance(result, DetectionAmbiguity):
                # La jugada está bien hecha en el tablero; la cámara no vio
                # cuál de las capturas fue. Que el humano elija en la UI.
                logger.warning(
                    "Captura ambigua: candidatas %s",
                    ", ".join(move.uci() for move in result.candidates),
                )
                self._clock_pause()  # la jugada física ya está: no corre más
                self._pending_choices = list(result.candidates)
                self._last_error = result.message
                self._phase = MatchPhase.HUMAN_CHOICE
                self._say(Event.AMBIGUOUS_MOVE)
                return self.status()

            assert isinstance(result, DetectionResult)
            self._clock_pause()  # jugada válida: deja de correr su tiempo
            robot_moves = self._finish_human_move(result.move)

        if robot_moves:
            self._robot_turn()
        return self.status()

    def choose(self, uci: str) -> dict:
        """El humano eligió entre las capturas indistinguibles (HUMAN_CHOICE)."""
        with self._lock:
            if self._phase != MatchPhase.HUMAN_CHOICE:
                self._last_error = "No hay ninguna jugada esperando elección."
                return self.status()
            move = next((m for m in self._pending_choices if m.uci() == uci), None)
            if move is None:
                self._last_error = "Esa jugada no está entre las detectadas."
                return self.status()
            logger.info("Captura ambigua resuelta por el humano: %s", move.uci())
            self._pending_choices = []
            robot_moves = self._finish_human_move(move)

        if robot_moves:
            self._robot_turn()
        return self.status()

    def _finish_human_move(self, move: chess.Move) -> bool:
        """Aplica la jugada humana ya resuelta (con ``self._lock`` tomado).
        Devuelve True si sigue el turno del robot (llamarlo fuera del lock)."""
        board_before = self._game.board.copy(stack=False)
        evaluation_before = self._evaluation
        self._game.apply_move(move)
        self._last_error = None
        self._update_evaluation()

        if self._game.outcome() is not None:
            self._phase = MatchPhase.GAME_OVER
            self._record_game()
            self._announce_outcome()
            return False
        self._comment_human_move(board_before, move, evaluation_before)
        self._phase = MatchPhase.ROBOT_TURN
        return True

    def resync_check(self) -> bool:
        """En RESYNC: si el tablero ya coincide, reanuda. Devuelve si reanudó."""
        with self._lock:
            if self._phase != MatchPhase.RESYNC:
                return False
            sensor_bitmap = self._scanner.latest
            if sensor_bitmap != self._game.expected_bitmap:
                return False
            self._last_error = None
            if self._game.is_human_turn and not self._self_play:
                self._enter_human_turn()
                return True
            self._phase = MatchPhase.ROBOT_TURN
        self._robot_turn()
        return True

    # ---------------------------------------------------------------- interno

    def _auto_resync(self, bitmap: Bitmap) -> None:
        """Suscriptor del scanner: en RESYNC, si el bitmap estable coincide
        con lo esperado, reanuda la partida. Corre en el hilo del scanner,
        así que la reanudación (motor + robot) se lanza en un hilo aparte
        para no bloquear el barrido."""
        with self._lock:
            matches = (
                self._phase == MatchPhase.RESYNC
                and bitmap == self._game.expected_bitmap
            )
        if matches:
            threading.Thread(
                target=self.resync_check, name="auto-resync", daemon=True
            ).start()

    def _schedule_robot_move(self, game_id: int, delay: float) -> None:
        """Demo robot vs robot: lanza la siguiente jugada en un hilo aparte
        tras ``delay`` segundos. La pausa se corta si la partida se detiene o
        se reemplaza (``_cancel``), y el hilo verifica que siga siendo la
        misma partida y el turno del robot antes de mover."""
        cancel = self._cancel

        def run() -> None:
            if cancel.wait(delay):
                return
            with self._lock:
                if self._game_id != game_id or self._phase != MatchPhase.ROBOT_TURN:
                    return
            self._robot_turn()

        threading.Thread(target=run, name="self-play", daemon=True).start()

    def _record_game(self, result: str | None = None) -> None:
        """Partida terminada: log completo y, si compite, el ranking.

        ``result`` fuerza el resultado cuando el tablero no lo determina
        (abandono del humano → ``"loss"``)."""
        outcome = self._game.outcome()
        if self._loss_reason:
            termination = self._loss_reason  # RESIGNATION / TIMEOUT
            chess_result = "1-0" if self._robot_is_white() else "0-1"
        else:
            termination = outcome.termination if outcome else "ABORTED"
            chess_result = outcome.result if outcome else None
        if self._self_play:
            # La demo no compite en el ranking, pero al log va igual.
            self._log_game(
                result=None,
                chess_result=chess_result,
                termination=termination,
                score=None,
            )
            return
        if result is None:
            if outcome is None:
                return
            if outcome.winner is None:
                result = "draw"
            elif outcome.winner == self._game.human_color:
                result = "win"
            else:
                result = "loss"

        moves = self._game.board.move_stack
        human_moves = sum(
            1 for i, _ in enumerate(moves)
            if (i % 2 == 0) == (self._game.human_color == chess.WHITE)
        )
        # Material capturado al robot: 39 iniciales menos lo que le queda.
        robot_color = not self._game.human_color
        remaining = sum(
            _PIECE_VALUES.get(piece.piece_type, 0)
            for piece in self._game.board.piece_map().values()
            if piece.color == robot_color
        )
        material = max(0, 39 - remaining)
        difficulty = getattr(self._engine, "difficulty", "aleatorio")
        score = compute_score(result, human_moves, material, difficulty)

        self._last_game = {
            "player_name": self._player_name,
            "score": score,
            "result": result,
            "difficulty": difficulty,
            "moves": human_moves,
            "material": material,
        }
        if self._scores is not None:
            try:
                self._scores.record(
                    name=self._player_name or "Anónimo",
                    score=score,
                    result=result,
                    difficulty=difficulty,
                    moves=human_moves,
                    material=material,
                )
            except Exception:
                logger.exception("No se pudo registrar el puntaje")
        log_result = {"RESIGNATION": "abandoned", "TIMEOUT": "timeout"}.get(
            self._loss_reason, result
        )
        self._log_game(
            result=log_result,
            chess_result=chess_result,
            termination=termination,
            score=score,
        )

    def _log_game(
        self,
        *,
        result: str | None,
        chess_result: str | None,
        termination: str,
        score: int | None,
    ) -> None:
        """Registro completo de la partida para el sistema de supervisión."""
        if self._scores is None:
            return
        ended = datetime.now()
        started = self._started_at or ended
        robot_color = not self._game.human_color
        remaining = sum(
            _PIECE_VALUES.get(piece.piece_type, 0)
            for piece in self._game.board.piece_map().values()
            if piece.color == robot_color
        )
        try:
            self._scores.log_game(
                started_ts=started.isoformat(timespec="seconds"),
                ended_ts=ended.isoformat(timespec="seconds"),
                day=ended.strftime("%Y-%m-%d"),
                duration_s=int((ended - started).total_seconds()),
                mode="self_play" if self._self_play else "human",
                player_name=self._player_name,
                human_color=(
                    None
                    if self._self_play
                    else ("white" if self._game.human_color == chess.WHITE else "black")
                ),
                difficulty=getattr(self._engine, "difficulty", "aleatorio"),
                result=result,
                chess_result=chess_result,
                termination=termination,
                moves=len(self._game.board.move_stack),
                san=" ".join(self._game.san_history),
                final_fen=self._game.fen,
                score=score,
                material=max(0, 39 - remaining),
            )
        except Exception:
            logger.exception("No se pudo registrar la partida en el log")

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
        self._clock_resume()

    def _robot_turn(self) -> None:
        with self._lock:
            if self._phase != MatchPhase.ROBOT_TURN:
                return
            game_id = self._game_id
            self_play = self._self_play
            board_before = self._game.board.copy(stack=False)
            move = self._engine.choose_move(board_before)
            logger.info("Robot juega %s", move.uci())
            robot_failed = False
            try:
                # Demo: sin vuelta a la posición de espera entre jugadas.
                self._robot_steps = self._robot.execute_move(
                    board_before, move, park=not self_play
                )
            except Exception:
                robot_failed = True
                # Robot aún no conectado o movimiento fallido: la jugada del
                # motor vale igual — si nadie la ejecuta en el tablero, la
                # verificación lleva a RESYNC (que se auto-recupera).
                logger.exception("El robot no pudo ejecutar %s", move.uci())
                self._robot_steps = []
                self._last_error = (
                    f"El robot no pudo mover ({move.uci()}): ejecutar la jugada a mano."
                )
            self._game.apply_move(move)
            self._update_evaluation()
            expected = self._game.expected_bitmap
            if self._on_robot_moved is not None:
                self._on_robot_moved(expected)
            if self._game.outcome() is None:
                self._comment_robot_move(board_before, move)

        if self_play:
            # Demo: no se espera a la cámara entre jugadas. Solo si el robot
            # no pudo mover, esperar a que el operador coloque la jugada.
            if robot_failed:
                with self._lock:
                    if self._game_id != game_id:
                        return
                    self._phase = MatchPhase.RESYNC
                self.resync_check()
                return
        # Verificación física fuera del lock (espera al scanner).
        elif not self._scanner.wait_for_bitmap(expected, timeout=ROBOT_VERIFY_TIMEOUT_S):
            with self._lock:
                if self._game_id != game_id:
                    return  # la partida se detuvo/reinició mientras verificaba
                self._last_error = (
                    "El tablero no refleja la jugada del robot "
                    "(¿pieza mal agarrada?). Corregir y continuar."
                )
                self._phase = MatchPhase.RESYNC
            # Cierra la carrera timeout↔corrección: si justo ahora ya
            # coincide, reanudar sin esperar otro cambio del scanner.
            if not self.resync_check():
                self._say(Event.RESYNC)
            return

        with self._lock:
            if self._game_id != game_id:
                return
            if self._game.outcome() is not None:
                self._phase = MatchPhase.GAME_OVER
                self._record_game()
                self._announce_outcome()
                if self_play:
                    try:
                        self._robot.park()  # fin de la demo: despejar el tablero
                    except Exception:
                        logger.warning("No se pudo ir a la posición de espera", exc_info=True)
                return
            if not self._self_play:
                self._enter_human_turn()
                return
            # Demo: sigue siendo turno del robot; la próxima jugada va con pausa.
            self._phase = MatchPhase.ROBOT_TURN
        self._schedule_robot_move(game_id, delay=SELF_PLAY_DELAY_S)

    # ------------------------------------------------------------------ reloj

    def _clock_resume(self) -> None:
        if self._clock_limit_s is not None and self._clock_run_since is None:
            self._clock_run_since = time.monotonic()

    def _clock_pause(self) -> None:
        if self._clock_run_since is not None:
            self._clock_used_s += time.monotonic() - self._clock_run_since
            self._clock_run_since = None

    def _clock_remaining_s(self) -> float | None:
        if self._clock_limit_s is None:
            return None
        used = self._clock_used_s
        if self._clock_run_since is not None:
            used += time.monotonic() - self._clock_run_since
        return max(0.0, self._clock_limit_s - used)

    def _start_clock_watcher(self, game_id: int) -> None:
        """Hilo que declara la derrota por tiempo cuando el reloj llega a 0."""
        cancel = self._cancel

        def run() -> None:
            while not cancel.wait(0.25):
                with self._lock:
                    if self._game_id != game_id:
                        return
                    remaining = self._clock_remaining_s()
                    if remaining is None:
                        return
                if remaining <= 0:
                    self._timeout(game_id)
                    return

        threading.Thread(target=run, name="game-clock", daemon=True).start()

    def _timeout(self, game_id: int) -> None:
        with self._lock:
            if self._game_id != game_id:
                return
            if self._phase not in (MatchPhase.HUMAN_TURN, MatchPhase.HUMAN_ERROR):
                return  # el reloj solo puede caer en el turno del humano
            remaining = self._clock_remaining_s()
            if remaining is None or remaining > 0:
                return
            self._clock_pause()
            self._bridge.stop()
            self._loss_reason = "TIMEOUT"
            self._phase = MatchPhase.GAME_OVER
            self._last_error = None
            self._record_game(result="loss")
            try:
                self._robot.park()
            except Exception:
                logger.warning("No se pudo ir a la posición de espera", exc_info=True)
        self._say(Event.TIMEOUT)

    # ------------------------------------------------------------------- voz

    def _say(self, event: Event) -> None:
        if self._commentator is not None:
            self._commentator.say(event, self_play=self._self_play)

    def _robot_is_white(self) -> bool:
        return self._game.human_color == chess.BLACK

    @staticmethod
    def _white_cp(evaluation: dict | None) -> int | None:
        """Evaluación en centipeones desde las blancas (mate = ±10000)."""
        if not evaluation:
            return None
        if evaluation.get("mate") is not None:
            return 10000 if evaluation["mate"] > 0 else -10000
        return evaluation.get("cp")

    def _robot_cp_delta(self, before: dict | None, after: dict | None) -> int | None:
        """Cuánto mejoró la posición del robot entre dos evaluaciones."""
        cp_before = self._white_cp(before)
        cp_after = self._white_cp(after)
        if cp_before is None or cp_after is None:
            return None
        sign = 1 if self._robot_is_white() else -1
        return (cp_after - cp_before) * sign

    def _comment_human_move(
        self, board_before: chess.Board, move: chess.Move, evaluation_before: dict | None
    ) -> None:
        if self._commentator is None or self._self_play:
            return
        delta = self._robot_cp_delta(evaluation_before, self._evaluation)
        if board_before.gives_check(move):
            self._say(Event.HUMAN_CHECK)
        elif delta is not None and delta >= 150:
            self._say(Event.HUMAN_BLUNDER)
        elif board_before.is_capture(move):
            self._say(Event.HUMAN_CAPTURE)
        elif delta is not None and delta <= -100:
            self._say(Event.HUMAN_GOOD_MOVE)

    def _comment_robot_move(self, board_before: chess.Board, move: chess.Move) -> None:
        if self._commentator is None or self._self_play:
            return
        evaluation = self._evaluation or {}
        mate = evaluation.get("mate")
        mate_for_robot = mate is not None and mate != 0 and (mate > 0) == self._robot_is_white()
        cp = self._white_cp(evaluation)
        robot_cp = None if cp is None else cp * (1 if self._robot_is_white() else -1)
        if mate_for_robot and abs(mate) <= 5:
            self._say(Event.ROBOT_MATE_SOON)
        elif board_before.gives_check(move):
            self._say(Event.ROBOT_CHECK)
        elif board_before.is_capture(move):
            self._say(Event.ROBOT_CAPTURE)
        elif move.promotion is not None:
            self._say(Event.ROBOT_PROMOTION)
        elif board_before.is_castling(move):
            self._say(Event.ROBOT_CASTLE)
        elif robot_cp is not None and robot_cp <= -300:
            self._say(Event.ROBOT_BEHIND)
        else:
            self._say(Event.ROBOT_MOVE)

    def _announce_outcome(self) -> None:
        if self._commentator is None or self._self_play:
            return
        outcome = self._game.outcome()
        if outcome is None:
            return
        if outcome.winner is None:
            self._say(Event.DRAW)
        elif outcome.winner == self._game.human_color:
            self._say(Event.ROBOT_LOSES)
        else:
            self._say(Event.ROBOT_WINS)
