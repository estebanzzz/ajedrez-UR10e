"""LazyURRobot: conexión perezosa con reintentos hasta que el UR aparece."""

import time

import pytest

from app.robot_controller import robot as robot_mod
from app.robot_controller.geometry import Point3
from app.robot_controller.robot import LazyURRobot, Pose


class FlakyUR:
    """Simula el UR booteando: falla N intentos y luego conecta."""

    attempts = 0
    fail_times = 2

    def __init__(self, host: str) -> None:
        cls = type(self)
        cls.attempts += 1
        if cls.attempts <= cls.fail_times:
            raise RuntimeError("robot booteando")
        self.host = host
        self.moves: list[Pose] = []

    def status(self) -> dict:
        return {"simulated": False, "connected": True}

    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        self.moves.append(pose)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


def _wait_connected(lazy: LazyURRobot, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lazy.status().get("connected"):
            return True
        time.sleep(0.02)
    return False


def test_lazy_ur_retries_until_connected(monkeypatch):
    FlakyUR.attempts = 0
    monkeypatch.setattr(robot_mod, "URRtdeRobot", FlakyUR)
    monkeypatch.setattr(LazyURRobot, "RETRY_INTERVAL_S", 0.05)

    lazy = LazyURRobot("10.0.0.1")
    try:
        assert _wait_connected(lazy)
        assert FlakyUR.attempts == FlakyUR.fail_times + 1
        # Conectado: delega sin error.
        lazy.move_linear(Pose(Point3(0.4, 0.0, 0.3)), 0.1, 0.1)
    finally:
        lazy.close()


def test_lazy_ur_reports_error_and_raises_before_connect(monkeypatch):
    class NeverUR:
        def __init__(self, host: str) -> None:
            raise RuntimeError("sin robot")

    monkeypatch.setattr(robot_mod, "URRtdeRobot", NeverUR)
    monkeypatch.setattr(LazyURRobot, "RETRY_INTERVAL_S", 10.0)

    lazy = LazyURRobot("10.0.0.1")
    try:
        time.sleep(0.1)  # dejar correr el primer intento
        status = lazy.status()
        assert status["connected"] is False
        assert "no conectado" in status["error"]
        with pytest.raises(RuntimeError, match="no conectado"):
            lazy.get_tcp_pose()
    finally:
        lazy.close()
