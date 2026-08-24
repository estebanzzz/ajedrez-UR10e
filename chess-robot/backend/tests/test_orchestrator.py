"""Fase 4: partida completa end-to-end con sensores simulados y robot simulado.

El mundo físico se simula así: las jugadas humanas se ejecutan como secuencias
de snapshots sobre el MockDriver; cuando el robot "mueve", el callback
``on_robot_moved`` deja el bitmap esperado en el driver (con hardware real lo
haría la física).
"""

import time

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

    def __init__(
        self,
        engine_moves: list[str],
        drop_robot_pieces: bool = False,
        scores=None,
        commentator=None,
    ):
        self.driver = MockDriver(initial=chess.Board().occupied)
        self.scanner = BoardScanner(self.driver, scan_hz=200.0, stable_reads=2)
        self.game = GameState()
        from app.robot_controller import MotionParams

        robot = RobotController(
            SimulatedRobot(), BOARD_GEO, TRAY, RESERVE,
            motion=MotionParams(grip_settle_s=0.0),
        )
        on_moved = None if drop_robot_pieces else self.driver.set_bitmap
        self.orchestrator = GameOrchestrator(
            self.game,
            self.scanner,
            ScriptedEngine(engine_moves),
            robot,
            on_moved,
            scores=scores,
            commentator=commentator,
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


def test_game_log_records_finished_resigned_and_aborted(tmp_path):
    from app.scores import ScoreStore

    store = ScoreStore(tmp_path / "scores.db")
    # Jugadas del robot: partida 1 (mate del pastor), partida 2 (e5 tras e4)
    # y partida 3 (d5 tras d4).
    rig = Rig(["e7e5", "b8c6", "g8f6", "e7e5", "d7d5"], scores=store)
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE, player_name="Ana")
        for uci in ["e2e4", "f1c4", "d1h5", "h5f7"]:
            rig.human_plays(uci)
        # Partida 2: abandono a mitad de camino.
        rig.orchestrator.new_game(human_color=chess.WHITE, player_name="Beto")
        rig.human_plays("e2e4")
        rig.orchestrator.resign()
        # Partida 3: el operador la corta con jugadas hechas.
        rig.orchestrator.new_game(human_color=chess.WHITE, player_name="Caro")
        rig.human_plays("d2d4")
        rig.orchestrator.stop_game()
    finally:
        rig.stop()

    games = store.list_games()["games"]
    assert [g["result"] for g in games] == ["aborted", "abandoned", "win"]
    win = store.game_detail(games[2]["id"])
    assert win["termination"] == "CHECKMATE"
    assert win["chess_result"] == "1-0"
    assert win["san"].split()[-1] == "Qxf7#"
    assert win["score"] > 0 and win["duration_s"] >= 0
    abandoned = store.game_detail(games[1]["id"])
    assert abandoned["termination"] == "RESIGNATION"
    assert abandoned["moves"] == 2  # e4 y la respuesta del robot
    aborted = store.game_detail(games[0]["id"])
    assert aborted["chess_result"] is None and aborted["score"] is None
    store.close()


@pytest.mark.rig_args(engine_moves=["e7e5"])
def test_resign_ends_game_as_loss_with_score(rig):
    rig.orchestrator.new_game(human_color=chess.WHITE, player_name="Ana")
    rig.human_plays("e2e4")  # 1.e4 e5: hay jugadas y partida en curso

    status = rig.orchestrator.resign()
    assert status["phase"] == "game_over"
    assert status["outcome"] == {
        "termination": "RESIGNATION",
        "result": "0-1",
        "winner": "black",
    }
    assert status["last_game"]["result"] == "loss"
    assert status["last_game"]["score"] > 0  # el abandono no borra el puntaje

    # Sin partida en curso no hay nada que abandonar.
    status = rig.orchestrator.resign()
    assert status["last_error"] == "No hay partida que abandonar."

    # Una partida nueva limpia el abandono anterior.
    rig.orchestrator.new_game(human_color=chess.WHITE)
    assert rig.orchestrator.status()["outcome"] is None


def test_clock_times_out_as_timeout_loss(tmp_path):
    from app.scores import ScoreStore

    store = ScoreStore(tmp_path / "scores.db")
    rig = Rig(["e7e5"], scores=store)
    try:
        rig.orchestrator.new_game(
            human_color=chess.WHITE, player_name="Ana", time_limit_s=0.7
        )
        status = rig.orchestrator.status()
        assert status["clock"] is not None and status["clock"]["running"] is True

        deadline = time.time() + 3.0
        while time.time() < deadline and rig.orchestrator.phase != MatchPhase.GAME_OVER:
            time.sleep(0.05)

        status = rig.orchestrator.status()
        assert status["phase"] == "game_over"
        assert status["outcome"]["termination"] == "TIMEOUT"
        assert status["last_game"]["result"] == "loss"
        assert status["clock"]["running"] is False
        assert status["clock"]["remaining_s"] == 0
        logged = store.list_games()["games"][0]
        assert logged["result"] == "timeout"
        assert logged["termination"] == "TIMEOUT"
    finally:
        rig.stop()


@pytest.mark.rig_args(engine_moves=["e2e4"])  # la demo puede llegar a jugar
def test_clock_optional_and_absent_in_demo(rig):
    # Sin time_limit_s no hay reloj.
    rig.orchestrator.new_game(human_color=chess.WHITE)
    assert rig.orchestrator.status()["clock"] is None
    # La demo nunca lleva reloj, aunque se pida.
    rig.orchestrator.new_game(self_play=True, time_limit_s=300)
    assert rig.orchestrator.status()["clock"] is None
    rig.orchestrator.stop_game()


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


@pytest.mark.rig_args(engine_moves=["a7a6", "a6a5", "a5a4"])
def test_ambiguous_capture_asks_human_to_choose(rig):
    """Caso real (Nxh7/Nxf7): dos capturas desde el mismo origen dejan el
    mismo bitmap y la mano ocluyó el estado intermedio — no es ilegal, el
    humano elige cuál fue en la pantalla y la partida sigue."""
    rig.orchestrator.new_game(human_color=chess.WHITE)
    rig.human_plays("g1f3")  # ... a6
    rig.human_plays("f3e5")  # ... a5

    # El caballo de e5 puede comer en d7 o f7. Los sensores solo ven el
    # origen vaciarse (la mano tapó el destino durante el cambio de piezas).
    lifted = rig.game.board.occupied & ~(1 << chess.E5)
    rig.driver.set_bitmap(lifted)
    assert rig.scanner.wait_for_bitmap(lifted, timeout=1.0)
    status = rig.orchestrator.confirm()

    assert status["phase"] == "human_choice"
    assert {c["san"] for c in status["pending_choices"]} == {"Nxd7", "Nxf7"}
    assert "elegí" in status["last_error"]

    # Una jugada que no está entre las candidatas no vale.
    status = rig.orchestrator.choose("e2e4")
    assert status["phase"] == "human_choice"
    assert status["last_error"] == "Esa jugada no está entre las detectadas."

    # El humano toca Nxf7: se aplica y el robot responde.
    status = rig.orchestrator.choose("e5f7")
    assert status["phase"] == "human_turn"
    assert status["pending_choices"] == []
    assert status["san_history"] == ["Nf3", "a6", "Ne5", "a5", "Nxf7", "a4"]
    assert rig.scanner.latest == rig.game.expected_bitmap


@pytest.mark.rig_args(engine_moves=["e7e5"])
def test_pause_stops_clock_and_blocks_moves(rig):
    rig.orchestrator.new_game(human_color=chess.WHITE, time_limit_s=300)
    status = rig.orchestrator.pause()
    assert status["phase"] == "paused"
    assert status["clock"]["running"] is False
    remaining = status["clock"]["remaining_s"]

    # Confirmar durante la pausa no aplica jugadas.
    status = rig.orchestrator.confirm()
    assert status["phase"] == "paused"
    assert status["last_error"] == "No es el turno del humano."

    time.sleep(0.3)
    status = rig.orchestrator.resume()
    assert status["phase"] == "human_turn"
    assert status["clock"]["running"] is True
    assert status["clock"]["remaining_s"] >= remaining - 1  # no corrió en pausa

    # La partida sigue normal tras reanudar.
    status = rig.human_plays("e2e4")
    assert status["san_history"] == ["e4", "e5"]


@pytest.mark.rig_args(engine_moves=["a7a6", "a6a5", "a5a4"])
def test_pause_preserves_pending_choice(rig):
    """Pausa durante la elección de una captura ambigua: al reanudar, las
    candidatas siguen ahí, el reloj sigue pausado (la jugada ya está hecha)
    y la elección funciona normal."""
    rig.orchestrator.new_game(human_color=chess.WHITE, time_limit_s=300)
    rig.human_plays("g1f3")  # ... a6
    rig.human_plays("f3e5")  # ... a5
    lifted = rig.game.board.occupied & ~(1 << chess.E5)
    rig.driver.set_bitmap(lifted)
    assert rig.scanner.wait_for_bitmap(lifted, timeout=1.0)
    assert rig.orchestrator.confirm()["phase"] == "human_choice"

    status = rig.orchestrator.pause()
    assert status["phase"] == "paused"
    assert status["clock"]["running"] is False
    status = rig.orchestrator.pause()  # pausar dos veces no rompe nada
    assert status["phase"] == "paused"
    assert "pausar" in status["last_error"]

    status = rig.orchestrator.resume()
    assert status["phase"] == "human_choice"
    assert status["clock"]["running"] is False  # en la elección no corre
    assert {c["san"] for c in status["pending_choices"]} == {"Nxd7", "Nxf7"}
    status = rig.orchestrator.choose("e5d7")
    assert status["phase"] == "human_turn"
    assert status["san_history"][-2:] == ["Nxd7", "a4"]


@pytest.mark.rig_args(engine_moves=[])
def test_pause_needs_running_game_and_resume_needs_pause(rig):
    status = rig.orchestrator.pause()  # sin partida en curso
    assert status["phase"] == "idle"
    assert "pausar" in status["last_error"]

    rig.orchestrator.new_game(human_color=chess.WHITE)
    status = rig.orchestrator.resume()  # sin pausa previa
    assert status["phase"] == "human_turn"
    assert status["last_error"] == "La partida no está en pausa."


@pytest.mark.rig_args(engine_moves=[])
def test_choose_without_pending_choice_is_rejected(rig):
    rig.orchestrator.new_game(human_color=chess.WHITE)
    status = rig.orchestrator.choose("e2e4")
    assert status["phase"] == "human_turn"
    assert status["last_error"] == "No hay ninguna jugada esperando elección."


@pytest.mark.rig_args(engine_moves=["e7e5"], drop_robot_pieces=True)
def test_robot_failure_enters_resync_and_auto_recovers(rig, monkeypatch):
    # Verificación corta para no demorar el test.
    monkeypatch.setattr("app.game_state.orchestrator.ROBOT_VERIFY_TIMEOUT_S", 0.2)
    rig.orchestrator.new_game(human_color=chess.WHITE)
    status = rig.human_plays("e2e4")

    # El robot "movió" pero el tablero físico no cambió → resync.
    assert status["phase"] == "resync"
    assert "e7" in status["mismatched_squares"] and "e5" in status["mismatched_squares"]
    assert not rig.orchestrator.resync_check()  # aún sin corregir

    # Al colocar las piezas como corresponde, la partida se reanuda SOLA
    # (suscriptor del scanner), sin botón del operador.
    rig.driver.set_bitmap(rig.game.expected_bitmap)
    deadline = time.monotonic() + 2.0
    while (
        time.monotonic() < deadline
        and rig.orchestrator.phase != MatchPhase.HUMAN_TURN
    ):
        time.sleep(0.02)
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


def test_finished_game_records_score(tmp_path):
    from app.scores import ScoreStore

    scores = ScoreStore(tmp_path / "scores.db")
    rig = Rig(["e7e5", "b8c6", "g8f6"], scores=scores)
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE, player_name="Esteban Z")
        for uci in ["e2e4", "f1c4", "d1h5"]:
            rig.human_plays(uci)
        status = rig.human_plays("h5f7")  # Qxf7#

        assert status["phase"] == "game_over"
        last = status["last_game"]
        assert last["player_name"] == "Esteban Z"
        assert last["result"] == "win"
        assert last["moves"] == 4
        assert last["material"] == 1  # el peón de f7
        assert last["score"] > 1000

        ranking = scores.top_today()
        assert ranking[0]["name"] == "Esteban Z"
        assert ranking[0]["score"] == last["score"]
    finally:
        rig.stop()


# ------------------------------------------------------ demo robot vs robot


def _wait_phase(orchestrator, phase: MatchPhase, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and orchestrator.phase != phase:
        time.sleep(0.02)
    assert orchestrator.phase == phase, orchestrator.status()


# Mate del loco: 4 jugadas, todas del "robot".
@pytest.mark.rig_args(engine_moves=["f2f3", "e7e5", "g2g4", "d8h4"])
def test_self_play_runs_whole_game_without_human(rig, monkeypatch):
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.05)
    rig.orchestrator.new_game(self_play=True)
    # La llamada vuelve enseguida: la partida corre en un hilo aparte.
    status = rig.orchestrator.status()
    assert status["mode"] == "self_play"
    assert status["phase"] in ("robot_turn", "game_over")

    _wait_phase(rig.orchestrator, MatchPhase.GAME_OVER)
    status = rig.orchestrator.status()
    assert status["san_history"] == ["f3", "e5", "g4", "Qh4#"]
    assert status["outcome"]["winner"] == "black"
    assert status["last_game"] is None  # la demo no entra al ranking
    assert rig.scanner.wait_for_bitmap(rig.game.expected_bitmap, timeout=1.0)
    # Nunca se armó el detector de jugada humana.
    assert status["detector_phase"] == "idle"


@pytest.mark.rig_args(engine_moves=["g1f3", "g8f6", "f3g1", "f6g8"] * 10)
def test_self_play_can_be_stopped(rig, monkeypatch):
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.05)
    rig.orchestrator.new_game(self_play=True)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and len(rig.game.san_history) < 2:
        time.sleep(0.02)
    assert len(rig.game.san_history) >= 2

    status = rig.orchestrator.stop_game()
    assert status["phase"] == "idle"
    assert status["mode"] == "human"
    played = len(rig.game.san_history)
    time.sleep(0.3)  # cualquier hilo diferido debe abortar sin mover
    assert rig.orchestrator.phase == MatchPhase.IDLE
    assert len(rig.game.san_history) == played


@pytest.mark.rig_args(engine_moves=["g1f3", "g8f6", "f3g1", "f6g8"] * 3)
def test_self_play_claims_threefold_repetition(rig, monkeypatch):
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.01)
    rig.orchestrator.new_game(self_play=True)
    _wait_phase(rig.orchestrator, MatchPhase.GAME_OVER, timeout=5.0)
    status = rig.orchestrator.status()
    assert status["outcome"]["termination"] == "THREEFOLD_REPETITION"
    assert status["outcome"]["winner"] is None


def _wait_moves(rig, count: int, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(rig.game.san_history) < count:
        time.sleep(0.02)
    assert len(rig.game.san_history) >= count, rig.orchestrator.status()


@pytest.mark.rig_args(
    engine_moves=["e2e4", "e7e5", "g1f3", "b8c6"], drop_robot_pieces=True
)
def test_self_play_does_not_wait_for_camera_between_moves(rig, monkeypatch):
    """Demo dinámica: el mundo físico simulado no cambia (drop_robot_pieces)
    y aun así la partida avanza — solo se verifica la posición inicial."""
    monkeypatch.setattr("app.game_state.orchestrator.ROBOT_VERIFY_TIMEOUT_S", 5.0)
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.05)
    rig.orchestrator.new_game(self_play=True)
    _wait_moves(rig, 3, timeout=2.0)
    assert rig.orchestrator.phase != MatchPhase.RESYNC
    # Sin vuelta a la espera entre jugadas: el brazo queda en altura segura.
    assert rig.orchestrator.status()["robot_steps"][-1] == "tránsito: brazo en altura segura"
    rig.orchestrator.stop_game()


@pytest.mark.rig_args(engine_moves=["e2e4", "e7e5", "g1f3", "b8c6"])
def test_self_play_requires_initial_position(rig, monkeypatch):
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.05)
    # Falta el peón de e2: la demo no arranca hasta que esté.
    missing = chess.Board().occupied & ~(1 << chess.E2)
    rig.driver.set_bitmap(missing)
    assert rig.scanner.wait_for_bitmap(missing, timeout=1.0)

    rig.orchestrator.new_game(self_play=True)
    status = rig.orchestrator.status()
    assert status["phase"] == "resync"
    assert "posición inicial" in status["last_error"]
    assert status["mismatched_squares"] == ["e2"]
    time.sleep(0.2)
    assert rig.game.san_history == []

    # Al completar el tablero arranca sola, en turno del robot.
    rig.driver.set_bitmap(chess.Board().occupied)
    _wait_moves(rig, 2)
    assert rig.orchestrator.phase != MatchPhase.HUMAN_TURN
    rig.orchestrator.stop_game()


@pytest.mark.rig_args(engine_moves=["e2e4", "e7e5", "g1f3", "b8c6"])
def test_self_play_robot_failure_enters_resync_then_resumes(rig, monkeypatch):
    monkeypatch.setattr("app.game_state.orchestrator.SELF_PLAY_DELAY_S", 0.05)
    robot = rig.orchestrator._robot
    real_execute = robot.execute_move
    calls = {"n": 0}

    def flaky_execute(board, move, park=True):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("moveL rechazado")
        return real_execute(board, move, park=park)

    monkeypatch.setattr(robot, "execute_move", flaky_execute)
    rig.orchestrator.new_game(self_play=True)
    # El robot no pudo mover e4 → resync con la jugada pendiente.
    _wait_phase(rig.orchestrator, MatchPhase.RESYNC)
    assert rig.game.san_history == ["e4"]
    assert "a mano" in rig.orchestrator.status()["last_error"]

    # El operador coloca e4 → la demo sigue sola en turno del robot.
    rig.driver.set_bitmap(rig.game.expected_bitmap)
    _wait_moves(rig, 2)
    assert rig.game.san_history[:2] == ["e4", "e5"]
    assert rig.orchestrator.phase != MatchPhase.HUMAN_TURN
    rig.orchestrator.stop_game()
