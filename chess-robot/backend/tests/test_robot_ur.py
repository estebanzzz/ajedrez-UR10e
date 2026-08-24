"""URRtdeRobot: recuperación tras una parada de protección/emergencia.

Tras la parada, el UR aborta el programa de control que sube ur_rtde pero el
socket RTDE sigue abierto: ``isConnected`` es True y cada ``moveL`` fallaba
con "RTDE control script is not running" hasta reiniciar el backend. El
driver debe detectar el programa caído y re-subirlo solo.
"""

import sys
import types

import pytest

from app.robot_controller.geometry import Point3
from app.robot_controller.robot import Pose


class FakeControl:
    """RTDEControlInterface mínimo: programa que se puede 'matar'."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.connected = True
        self.program_running = True
        self.reuploads = 0
        self.reconnects = 0
        self.moves: list[list[float]] = []
        self.reupload_ok = True

    def isConnected(self) -> bool:
        return self.connected

    def reconnect(self) -> bool:
        self.reconnects += 1
        self.connected = True
        self.program_running = True
        return True

    def isProgramRunning(self) -> bool:
        return self.program_running

    def reuploadScript(self) -> bool:
        self.reuploads += 1
        if self.reupload_ok:
            self.program_running = True
        return self.reupload_ok

    def moveL(self, target, speed, accel) -> bool:
        if not self.program_running:
            return False
        self.moves.append(list(target))
        return True

    def teachMode(self) -> bool:
        return True

    def endTeachMode(self) -> bool:
        return True

    def stopL(self) -> None:
        pass

    def stopScript(self) -> None:
        pass

    def disconnect(self) -> None:
        self.connected = False


class FakeReceive:
    def __init__(self, host: str) -> None:
        self.connected = True

    def isConnected(self) -> bool:
        return self.connected

    def reconnect(self) -> bool:
        self.connected = True
        return True

    def getActualTCPPose(self):
        return [0.4, 0.0, 0.3, 3.14, 0.0, 0.0]

    def getActualQ(self):
        return [0.0] * 6

    def getRobotMode(self) -> int:
        return 7

    def getSafetyMode(self) -> int:
        return 1

    def disconnect(self) -> None:
        self.connected = False


class NoGripper:
    connected = False

    def connect(self) -> None:
        raise OSError("sin garra")

    def close(self) -> None:
        pass


@pytest.fixture
def ur(monkeypatch):
    control_mod = types.ModuleType("rtde_control")
    control_mod.RTDEControlInterface = FakeControl
    receive_mod = types.ModuleType("rtde_receive")
    receive_mod.RTDEReceiveInterface = FakeReceive
    monkeypatch.setitem(sys.modules, "rtde_control", control_mod)
    monkeypatch.setitem(sys.modules, "rtde_receive", receive_mod)

    from app.robot_controller.robot import URRtdeRobot

    robot = URRtdeRobot("10.0.0.2", gripper=NoGripper())
    return robot, robot._control


def test_reuploads_control_script_after_protective_stop(ur):
    robot, control = ur
    robot.move_linear(Pose(Point3(0.4, 0.0, 0.3)), 0.1, 0.1)
    assert control.reuploads == 0

    # Parada de protección: el UR aborta el programa, el socket sigue vivo.
    control.program_running = False
    assert robot.status()["program_running"] is False

    robot.move_linear(Pose(Point3(0.4, 0.1, 0.3)), 0.1, 0.1)
    assert control.reuploads == 1
    assert control.reconnects == 0
    assert len(control.moves) == 2
    assert robot.status()["program_running"] is True


def test_clear_error_when_ur_still_locked(ur):
    robot, control = ur
    control.program_running = False
    control.reupload_ok = False  # la parada sigue sin destrabar
    with pytest.raises(RuntimeError, match="destrabar la parada"):
        robot.move_linear(Pose(Point3(0.4, 0.0, 0.3)), 0.1, 0.1)
    assert control.moves == []


def test_reconnects_when_socket_is_down(ur):
    robot, control = ur
    control.connected = False
    robot.move_linear(Pose(Point3(0.4, 0.0, 0.3)), 0.1, 0.1)
    assert control.reconnects == 1
    assert len(control.moves) == 1
