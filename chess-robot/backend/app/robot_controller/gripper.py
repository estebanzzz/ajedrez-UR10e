"""Garra Robotiq Hand-E vía el servidor socket del URCap.

El URCap de Robotiq levanta un servidor ASCII en el puerto 63352 del propio
robot que acepta ``GET``/``SET`` de las variables del controlador de la garra
(ACT, GTO, POS, SPE, FOR, STA, OBJ, ...). No requiere ningún programa
corriendo en el pendant, así que convive con el control por RTDE.

La Hand-E tiene 50 mm de carrera: posición cruda 0 = abierta, 255 = cerrada.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

log = logging.getLogger(__name__)


class GripperError(RuntimeError):
    """La garra no respondió o rechazó un comando."""


class RobotiqGripper:
    OPEN_MM = 50.0  # carrera nominal de la Hand-E
    _ACTIVATION_TIMEOUT_S = 12.0  # la activación hace un ciclo completo de cierre
    _MOVE_TIMEOUT_S = 5.0

    def __init__(self, host: str, port: int = 63352, timeout: float = 2.0) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- conexión

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._timeout)
        sock.connect((self._host, self._port))
        self._socket = sock

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    # ------------------------------------------------------------ protocolo

    def _command(self, text: str) -> str:
        with self._lock:
            if self._socket is None:
                raise GripperError("Garra no conectada")
            try:
                self._socket.sendall((text + "\n").encode("ascii"))
                data = self._socket.recv(1024)
            except OSError as exc:
                self.close()
                raise GripperError(f"Sin respuesta de la garra: {exc}") from exc
        if not data:
            self.close()
            raise GripperError("La garra cerró la conexión")
        return data.decode("ascii", errors="replace").strip()

    def _get(self, name: str) -> int:
        reply = self._command(f"GET {name}")
        try:
            var, value = reply.split()
            if var != name:
                raise ValueError
            return int(value)
        except ValueError:
            raise GripperError(f"Respuesta inesperada a GET {name}: {reply!r}")

    def _set(self, **variables: int) -> None:
        pairs = " ".join(f"{k} {v}" for k, v in variables.items())
        reply = self._command(f"SET {pairs}")
        if "ack" not in reply.lower():
            raise GripperError(f"SET {pairs} no confirmado: {reply!r}")

    # -------------------------------------------------------------- estado

    def is_active(self) -> bool:
        return self._get("STA") == 3

    def fault_code(self) -> int:
        return self._get("FLT")

    def activate(self) -> None:
        """Activa la garra (hace un ciclo de cierre/apertura de referencia)."""
        if self.is_active():
            return
        self._set(ACT=0)
        self._set(ACT=1)
        deadline = time.monotonic() + self._ACTIVATION_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.is_active():
                return
            time.sleep(0.2)
        raise GripperError(
            f"La garra no terminó de activarse (STA={self._get('STA')}, "
            f"FLT={self.fault_code()})"
        )

    def opening_mm(self) -> float:
        return self.OPEN_MM * (1.0 - self._get("POS") / 255.0)

    # ----------------------------------------------------------- movimiento

    def move_mm(
        self,
        opening_mm: float,
        force: float = 0.25,
        speed: float = 1.0,
        wait: bool = True,
    ) -> str:
        """Mueve a la apertura dada. ``force``/``speed`` relativos 0..1.

        Devuelve el resultado: "at_position", "object_closing", "object_opening".
        """
        raw_pos = round(255 * (1.0 - opening_mm / self.OPEN_MM))
        raw_pos = min(255, max(0, raw_pos))
        raw_force = min(255, max(0, round(force * 255)))
        raw_speed = min(255, max(0, round(speed * 255)))
        self._set(GTO=1, POS=raw_pos, SPE=raw_speed, FOR=raw_force)
        if not wait:
            return "moving"

        deadline = time.monotonic() + self._MOVE_TIMEOUT_S
        while time.monotonic() < deadline:
            obj = self._get("OBJ")
            if obj == 1:
                return "object_opening"
            if obj == 2:
                return "object_closing"
            if obj == 3:
                return "at_position"
            time.sleep(0.05)
        raise GripperError("Timeout esperando el fin del movimiento de la garra")
