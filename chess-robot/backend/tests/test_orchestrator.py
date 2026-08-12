"""Fase 4: partida completa end-to-end con sensores simulados y robot simulado.

El mundo físico se simula así: las jugadas humanas se ejecutan como secuencias
de snapshots sobre el MockDriver; cuando el robot "mueve", el callback
``on_robot_moved`` deja el bitmap esperado en el driver (con hardware real lo
haría la física).
"""

import chess
import pytest

from app.board_sensor import BoardScanner, MockDriver
from app.game_state import GameState
from app.game_state.orchestrator import GameOrchestrator, MatchPhase
from app.robot_controller import (
    BoardGeometry,
    Point3,
    RobotController,
    SimulatedRobot,
    TrayGrid,
)
from app.simulator.sim_sensor import snapshots_for_move

BOARD_GEO = BoardGeometry(
    a1=Point3(0.40, -0.175, 0.02),
    h1=Point3(0.75, -0.175, 0.02),
    a8=Point3(0.40, 0.175, 0.02),
    h8=Point3(0.75, 0.175, 0.02),
)
TRAY = TrayGrid(Point3(0.9, -0.2, 0.02), Point3(0.05, 0, 0), Point3(0, 0.05, 0), 4, 8)
RESERVE = TrayGrid(Point3(0.9, 0.3, 0.02), Point3(0.05, 0, 0), Point3(0, 0.05, 0), 2, 1)


class ScriptedEngine:
    """Motor determinista para tests: juega una lista fija de jugadas."""

    def __init__(self, ucis: list[str]) -> None:
        self._moves = [chess.Move.from_uci(u) for u in ucis]

    def choose_move(self, board: chess.Board) -> chess.Move:
        return self._moves.pop(0)

    def evaluate(self, board: chess.Board, time_limit: float = 0.3):
        from app.engine import Evaluation

        return Evaluation(centipawns=0, mate_in=None)


class Rig:
    """Banco de pruebas: orquestador completo sobre mundo simulado."""

    def __init__(self, engine_moves: list[str], drop_robot_pieces: bool = False):
        self.driver = MockDriver(initial=chess.Board().occupied)
        self.scanner = BoardScanner(self.driver, scan_hz=200.0, stable_reads=2)
        self.game = GameState()
        robot = RobotController(SimulatedRobot(), BOARD_GEO, TRAY, RESERVE)
        on_moved = None if drop_robot_pieces else self.driver.set_bitmap
        self.orchestrator = GameOrchestrator(
            self.game, self.scanner, ScriptedEngine(engine_moves), robot, on_moved
        )
        self.scanner.start()
        assert self.scanner.wait_for_bitmap(chess.Board().occupied, timeout=1.0)

    def stop(self) -> None:
        self.scanner.stop()

    def human_plays(self, uci: str) -> dict:
        """Ejecuta la jugada físicamente (snapshots) y pulsa confirmar."""
        move = chess.Move.from_uci(uci)
        for snapshot in snapshots_for_move(self.game.board, move):
            self.driver.set_bitmap(snapshot)
            assert self.scanner.wait_for_bitmap(snapshot, timeout=1.0)
        return self.orchestrator.confirm()


@pytest.fixture
def rig(request):
    marker = request.node.get_closest_marker("rig_args")
    kwargs = marker.kwargs if marker else {}
    engine_moves = kwargs.pop("engine_moves", [])
    r = Rig(engine_moves, **kwargs)
    yield r
    r.stop()


@pytest.mark.rig_args(engine_moves=["e7e5", "b8c6", "g8f6"])
def test_scholars_mate_end_to_end(rig):
    rig.orchestrator.new_game(human_color=chess.WHITE)
    assert rig.orchestrator.phase == MatchPhase.HUMAN_TURN

    for uci in ["e2e4", "f1c4", "d1h5"]:
        status = rig.human_plays(uci)
        assert status["phase"] == "human_turn", status
    status = rig.human_plays("h5f7")  # Qxf7# — captura y mate

    assert status["phase"] == "game_over"
    assert status["outcome"]["termination"] == "CHECKMATE"
    assert status["outcome"]["winner"] == "white"
    assert status["san_history"][-1] == "Qxf7#"
    # El mundo físico quedó consistente con la partida.
    assert rig.scanner.latest == rig.game.expected_bitmap


@pytest.mark.rig_args(engine_moves=["e7e5"])
def test_illegal_human_position_then_recovery(rig):
    rig.orchestrator.new_game(human_color=chess.WHITE)

    # El humano pone su peón en e5 (ilegal desde e2) y confirma.
    bad = (chess.Board().occupied & ~(1 << chess.E2)) | (1 << chess.E5)
    rig.driver.set_bitmap(bad)
    assert rig.scanner.wait_for_bitmap(bad, timeout=1.0)
    status = rig.orchestrator.confirm()
    assert status["phase"] == "human_error"
    assert status["last_error"] is not None
    assert set(status["mismatched_squares"]) == {"e2", "e5"}

    # Restaura y juega e4 correctamente: la partida sigue.
    rig.driver.set_bitmap(chess.Board().occupied)
    assert rig.scanner.wait_for_bitmap(chess.Board().occupied, timeout=1.0)
    status = rig.human_plays("e2e4")
    assert status["phase"] == "human_turn"
    assert status["san_history"] == ["e4", "e5"]


@pytest.mark.rig_args(engine_moves=["e7e5"], drop_robot_pieces=True)
def test_robot_failure_enters_resync_and_recovers(rig, monkeypatch):
    # Verificación corta para no demorar el test.
    monkeypatch.setattr("app.game_state.orchestrator.ROBOT_VERIFY_TIMEOUT_S", 0.2)
    rig.orchestrator.new_game(human_color=chess.WHITE)
    status = rig.human_plays("e2e4")

    # El robot "movió" pero el tablero físico no cambió → resync.
    assert status["phase"] == "resync"
    assert "e7" in status["mismatched_squares"] and "e5" in status["mismatched_squares"]
    assert not rig.orchestrator.resync_check()  # aún sin corregir

    # El operador coloca las piezas como corresponde.
    rig.driver.set_bitmap(rig.game.expected_bitmap)
    assert rig.scanner.wait_for_bitmap(rig.game.expected_bitmap, timeout=1.0)
    assert rig.orchestrator.resync_check()
    assert rig.orchestrator.phase == MatchPhase.HUMAN_TURN


@pytest.mark.rig_args(engine_moves=["e2e4", "d2d4"])
def test_robot_plays_white_when_human_is_black(rig):
    rig.orchestrator.new_game(human_color=chess.BLACK)
    # El robot ya jugó e4 y el mundo simulado lo refleja.
    assert rig.orchestrator.phase == MatchPhase.HUMAN_TURN
    status = rig.orchestrator.status()
    assert status["san_history"] == ["e4"]
    assert rig.scanner.latest == rig.game.expected_bitmap

    status = rig.human_plays("e7e5")
    assert status["san_history"] == ["e4", "e5", "d4"]


@pytest.mark.rig_args(engine_moves=[])
def test_confirm_out_of_turn_is_rejected(rig):
    status = rig.orchestrator.confirm()  # sin partida iniciada
    assert status["phase"] == "idle"
    assert status["last_error"] == "No es el turno del humano."
