"""Interfaz con el robot y la garra.

``RobotInterface`` es el contrato mínimo que usa el controlador de jugadas:
movimiento lineal del TCP y apertura/cierre de garra. Implementaciones:

- ``SimulatedRobot``: registra las acciones (tests y desarrollo sin robot).
- ``URRtdeRobot``: UR10e real vía ur_rtde + garra Robotiq por registros del
  URCap (pendiente de validar con hardware, Fase 3/4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.robot_controller.geometry import Point3


@dataclass(frozen=True)
class Pose:
    """Pose TCP: posición en metros + orientación como vector de rotación."""

    position: Point3
    rx: float = 3.1416  # herramienta vertical mirando hacia abajo (default UR)
    ry: float = 0.0
    rz: float = 0.0

    def at_height(self, z: float) -> "Pose":
        return Pose(Point3(self.position.x, self.position.y, z), self.rx, self.ry, self.rz)


class RobotInterface(Protocol):
    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        """moveL al pose dado (bloqueante)."""
        ...

    def gripper_move(self, opening_mm: float, force: float) -> None:
        """Mueve la garra a la apertura dada con la fuerza relativa (0..1)."""
        ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SimAction:
    kind: str  # "move" | "gripper" | "stop"
    pose: Pose | None = None
    speed: float | None = None
    opening_mm: float | None = None
    force: float | None = None


class SimulatedRobot:
    """Registra la secuencia de acciones para verificarla en tests/desarrollo."""

    def __init__(self) -> None:
        self.actions: list[SimAction] = []

    @property
    def moves(self) -> list[SimAction]:
        return [a for a in self.actions if a.kind == "move"]

    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        self.actions.append(SimAction(kind="move", pose=pose, speed=speed))

    def gripper_move(self, opening_mm: float, force: float) -> None:
        self.actions.append(SimAction(kind="gripper", opening_mm=opening_mm, force=force))

    def stop(self) -> None:
        self.actions.append(SimAction(kind="stop"))

    def close(self) -> None:
        pass


class URRtdeRobot:
    """UR10e real vía RTDE. Import diferido: ur_rtde solo está en la Pi.

    La garra Robotiq se comanda por los registros de entrada del UR con el
    URCap instalado (``rq_move`` en URScript). Pendiente de validar con el
    hardware; el mapeo exacto de registros se define en la puesta a punto.
    """

    def __init__(self, host: str) -> None:
        import rtde_control
        import rtde_io

        self._control = rtde_control.RTDEControlInterface(host)
        self._io = rtde_io.RTDEIOInterface(host)

    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        p = pose.position
        self._control.moveL([p.x, p.y, p.z, pose.rx, pose.ry, pose.rz], speed, acceleration)

    def gripper_move(self, opening_mm: float, force: float) -> None:
        # Convención provisoria: registro 18 = apertura (mm), 19 = fuerza (%).
        # El programa URCap del lado del robot lee estos registros y ejecuta
        # rq_move(); ajustar al integrar la garra real.
        self._io.setInputDoubleRegister(18, opening_mm)
        self._io.setInputDoubleRegister(19, force * 100.0)

    def stop(self) -> None:
        self._control.stopL()

    def close(self) -> None:
        self._control.disconnect()
