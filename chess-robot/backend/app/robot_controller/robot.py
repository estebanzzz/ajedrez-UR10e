"""Interfaz con el robot y la garra.

``RobotInterface`` es el contrato mínimo que usa el controlador de jugadas y
el asistente de calibración: movimiento lineal del TCP, lectura de pose,
freedrive y apertura/cierre de garra. Implementaciones:

- ``SimulatedRobot``: registra las acciones (tests y desarrollo sin robot).
- ``URRtdeRobot``: UR10e real vía ur_rtde + garra Robotiq Hand-E por el
  servidor socket del URCap (validado contra el hardware, PolyScope 5.25).

Requisitos del robot real: Control Remoto activado en el pendant y fieldbus
(EtherNet/IP, PROFINET, MODBUS) deshabilitados — ocupan los registros RTDE.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Protocol

from app.robot_controller.geometry import Point3
from app.robot_controller.gripper import GripperError, RobotiqGripper

log = logging.getLogger(__name__)


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

    def get_tcp_pose(self) -> Pose:
        """Pose TCP actual (para calibración y diagnóstico)."""
        ...

    def set_freedrive(self, enabled: bool) -> None:
        """Modo teach: el operador mueve el brazo a mano."""
        ...

    def gripper_move(self, opening_mm: float, force: float) -> None:
        """Mueve la garra a la apertura dada con la fuerza relativa (0..1)."""
        ...

    def status(self) -> dict:
        """Estado para la API de diagnóstico."""
        ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class SimAction:
    kind: str  # "move" | "gripper" | "stop" | "freedrive"
    pose: Pose | None = None
    speed: float | None = None
    opening_mm: float | None = None
    force: float | None = None
    enabled: bool | None = None


class SimulatedRobot:
    """Registra la secuencia de acciones para verificarla en tests/desarrollo."""

    def __init__(self, initial_pose: Pose | None = None) -> None:
        self.actions: list[SimAction] = []
        self.pose = initial_pose or Pose(Point3(0.4, 0.0, 0.3))
        self.freedrive = False

    @property
    def moves(self) -> list[SimAction]:
        return [a for a in self.actions if a.kind == "move"]

    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        self.actions.append(SimAction(kind="move", pose=pose, speed=speed))
        self.pose = pose

    def get_tcp_pose(self) -> Pose:
        return self.pose

    def set_freedrive(self, enabled: bool) -> None:
        self.freedrive = enabled
        self.actions.append(SimAction(kind="freedrive", enabled=enabled))

    def gripper_move(self, opening_mm: float, force: float) -> None:
        self.actions.append(SimAction(kind="gripper", opening_mm=opening_mm, force=force))

    def status(self) -> dict:
        p = self.pose
        return {
            "simulated": True,
            "connected": True,
            "freedrive": self.freedrive,
            "tcp_pose": [p.position.x, p.position.y, p.position.z, p.rx, p.ry, p.rz],
            "gripper": {"connected": True, "active": True, "simulated": True},
        }

    def stop(self) -> None:
        self.actions.append(SimAction(kind="stop"))

    def close(self) -> None:
        pass


class URRtdeRobot:
    """UR10e real vía RTDE. Import diferido: ur_rtde solo está donde hay robot.

    La garra es opcional: si no responde (por ejemplo, aún no está montada),
    el robot funciona igual y ``gripper_move`` queda en no-op con warning —
    ``status()`` refleja ``gripper.connected = False``.
    """

    def __init__(self, host: str, gripper: RobotiqGripper | None = None) -> None:
        import rtde_control
        import rtde_receive

        self._host = host
        self._lock = threading.Lock()
        self._control = rtde_control.RTDEControlInterface(host)
        self._receive = rtde_receive.RTDEReceiveInterface(host)
        self._freedrive = False

        self._gripper = gripper or RobotiqGripper(host)
        self._gripper_active = False
        try:
            self._gripper.connect()
        except OSError as exc:
            log.warning("Garra Robotiq no disponible en %s:63352: %s", host, exc)

    # ------------------------------------------------------------- conexión

    def _ensure_control(self) -> None:
        if not self._control.isConnected():
            log.warning("RTDE control desconectado; reconectando a %s", self._host)
            self._control.reconnect()

    def _ensure_receive(self) -> None:
        if not self._receive.isConnected():
            self._receive.reconnect()

    # ------------------------------------------------------------ movimiento

    def move_linear(self, pose: Pose, speed: float, acceleration: float) -> None:
        with self._lock:
            if self._freedrive:
                raise RuntimeError("Freedrive activo: desactivarlo antes de mover")
            self._ensure_control()
            p = pose.position
            target = [p.x, p.y, p.z, pose.rx, pose.ry, pose.rz]
            if not self._control.moveL(target, speed, acceleration):
                raise RuntimeError(f"moveL rechazado (destino {target})")

    def get_tcp_pose(self) -> Pose:
        self._ensure_receive()
        x, y, z, rx, ry, rz = self._receive.getActualTCPPose()
        return Pose(Point3(x, y, z), rx, ry, rz)

    def set_freedrive(self, enabled: bool) -> None:
        with self._lock:
            self._ensure_control()
            if enabled and not self._freedrive:
                if not self._control.teachMode():
                    raise RuntimeError("No se pudo activar el freedrive")
            elif not enabled and self._freedrive:
                if not self._control.endTeachMode():
                    raise RuntimeError("No se pudo desactivar el freedrive")
            self._freedrive = enabled

    # ---------------------------------------------------------------- garra

    def _ensure_gripper(self) -> None:
        if not self._gripper.connected:
            self._gripper.connect()  # OSError si sigue sin responder
        if not self._gripper_active:
            self._gripper.activate()
            self._gripper_active = True

    def gripper_move(self, opening_mm: float, force: float) -> None:
        try:
            self._ensure_gripper()
            result = self._gripper.move_mm(opening_mm, force=force)
            log.debug("Garra a %.1f mm: %s", opening_mm, result)
        except (OSError, GripperError) as exc:
            # Sin garra montada el juego no puede manipular piezas, pero los
            # ensayos de movimiento y la calibración sí deben poder seguir.
            log.warning("Garra no disponible (%s); se omite move a %.1f mm", exc, opening_mm)

    # ---------------------------------------------------------------- estado

    def status(self) -> dict:
        result: dict = {"simulated": False, "host": self._host}
        try:
            self._ensure_receive()
            pose = self._receive.getActualTCPPose()
            result.update(
                connected=True,
                freedrive=self._freedrive,
                tcp_pose=list(pose),
                joints_rad=list(self._receive.getActualQ()),
                robot_mode=self._receive.getRobotMode(),  # 7 = RUNNING
                safety_mode=self._receive.getSafetyMode(),  # 1 = NORMAL
            )
        except RuntimeError as exc:
            result.update(connected=False, error=str(exc))

        gripper: dict = {"connected": self._gripper.connected, "active": False}
        if self._gripper.connected:
            try:
                gripper["active"] = self._gripper.is_active()
                if gripper["active"]:
                    gripper["opening_mm"] = round(self._gripper.opening_mm(), 1)
            except GripperError as exc:
                gripper.update(connected=False, error=str(exc))
        result["gripper"] = gripper
        return result

    # ----------------------------------------------------------------- fin

    def stop(self) -> None:
        with self._lock:
            if self._freedrive:
                self._control.endTeachMode()
                self._freedrive = False
            self._control.stopL()

    def close(self) -> None:
        try:
            if self._freedrive:
                self._control.endTeachMode()
            self._control.stopScript()
        finally:
            self._control.disconnect()
            self._receive.disconnect()
            self._gripper.close()
