"""Integración scanner → move_detector: el turno humano leído desde sensores."""

import time

import chess

from app.board_sensor import BoardScanner, MockDriver
from app.move_detector import DetectionResult, DetectorPhase
from app.move_detector.bridge import SensorDetectorBridge
from app.simulator.sim_sensor import snapshots_for_move


def wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_human_move_detected_through_scanner():
    board = chess.Board()
    driver = MockDriver(initial=board.occupied)
    scanner = BoardScanner(driver, scan_hz=200.0, stable_reads=2)
    phases: list[DetectorPhase] = []
    bridge = SensorDetectorBridge(scanner, board, on_phase_change=phases.append)

    scanner.start()
    try:
        assert scanner.wait_for_bitmap(board.occupied, timeout=1.0)
        bridge.start_turn()

        # El humano ejecuta e2e4 físicamente: cada snapshot debe sostenerse
        # lo suficiente para pasar el debounce (como una mano real).
        move = chess.Move.from_uci("e2e4")
        for snapshot in snapshots_for_move(board, move):
            driver.set_bitmap(snapshot)
            assert scanner.wait_for_bitmap(snapshot, timeout=1.0)

        assert wait_until(lambda: bridge.phase == DetectorPhase.COMPLETE)
        result = bridge.confirm()
        assert isinstance(result, DetectionResult)
        assert result.move == move
        assert DetectorPhase.IN_PROGRESS in phases
        assert phases[-1] == DetectorPhase.COMPLETE
    finally:
        bridge.stop()
        scanner.stop()
