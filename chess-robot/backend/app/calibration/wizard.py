"""Asistente de calibración por teach: esquinas del tablero y bandejas.

Flujo: el operador pone el robot en freedrive, lleva el TCP (punta de la
garra cerrada) a cada punto indicado y captura la pose. Con los puntos se
construye ``CalibrationData``:

- Tablero: centros de a1, h1, a8, h8 tocando la superficie de la casilla.
- Bandejas: slot 0, slot adyacente de la misma fila (define ``col_step``) y
  primer slot de la fila siguiente (define ``row_step``; se omite si la
  bandeja tiene una sola fila).

La orientación de la herramienta no se captura: las jugadas usan la
orientación vertical por defecto de ``Pose``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.calibration.store import CalibrationData
from app.robot_controller.controller import MotionParams
from app.robot_controller.geometry import BoardGeometry, Point3, TrayGrid
from app.robot_controller.pieces import DEFAULT_PIECE_PARAMS
from app.robot_controller.robot import RobotInterface


@dataclass(frozen=True)
class WizardStep:
    key: str
    title: str
    instruction: str


def _board_step(square: str) -> WizardStep:
    return WizardStep(
        key=f"board_{square}",
        title=f"Tablero: casilla {square}",
        instruction=(
            f"Llevá la punta de la garra al CENTRO de la casilla {square}, "
            "tocando la superficie del tablero."
        ),
    )


def _distance(a: Point3, b: Point3) -> float:
    return math.dist((a.x, a.y, a.z), (b.x, b.y, b.z))


def _delta(a: Point3, b: Point3) -> Point3:
    return Point3(b.x - a.x, b.y - a.y, b.z - a.z)


class CalibrationWizard:
    """Sesión de captura de puntos. Estado en memoria; persiste al guardar."""

    def __init__(
        self,
        robot: RobotInterface,
        capture_cols: int = 4,
        capture_rows: int = 8,
        reserve_cols: int = 2,
        reserve_rows: int = 1,
        base: CalibrationData | None = None,
    ) -> None:
        self._robot = robot
        self._capture_grid = (capture_cols, capture_rows)
        self._reserve_grid = (reserve_cols, reserve_rows)
        self._base = base
        self._points: dict[str, Point3] = {}

        steps = [_board_step(sq) for sq in ("a1", "h1", "a8", "h8")]
        steps += self._tray_steps("capture", "bandeja de CAPTURAS", capture_rows)
        steps += self._tray_steps("reserve", "reserva de PROMOCIÓN", reserve_rows)
        self.steps: list[WizardStep] = steps

    @staticmethod
    def _tray_steps(prefix: str, label: str, rows: int) -> list[WizardStep]:
        steps = [
            WizardStep(
                key=f"{prefix}_origin",
                title=f"{label.capitalize()}: slot 0",
                instruction=(
                    f"Llevá la garra al centro del PRIMER slot de la {label}, "
                    "a la altura donde debe apoyar la base de la pieza."
                ),
            ),
            WizardStep(
                key=f"{prefix}_col",
                title=f"{label.capitalize()}: slot vecino (misma fila)",
                instruction=(
                    "Ahora al centro del slot ADYACENTE al anterior, "
                    "en la misma fila."
                ),
            ),
        ]
        if rows > 1:
            steps.append(
                WizardStep(
                    key=f"{prefix}_row",
                    title=f"{label.capitalize()}: primer slot de la fila 2",
                    instruction=(
                        "Por último, al PRIMER slot de la SEGUNDA fila "
                        "(el que está junto al slot 0, en la fila siguiente)."
                    ),
                )
            )
        return steps

    # ---------------------------------------------------------------- estado

    @property
    def current_step(self) -> WizardStep | None:
        index = len(self._points)
        return self.steps[index] if index < len(self.steps) else None

    @property
    def done(self) -> bool:
        return len(self._points) >= len(self.steps)

    def state(self) -> dict:
        current = self.current_step
        return {
            "steps": [
                {
                    "key": step.key,
                    "title": step.title,
                    "instruction": step.instruction,
                    "captured": (
                        vars(self._points[step.key]) if step.key in self._points else None
                    ),
                }
                for step in self.steps
            ],
            "current": current.key if current else None,
            "done": self.done,
        }

    # -------------------------------------------------------------- captura

    def capture(self) -> dict:
        step = self.current_step
        if step is None:
            raise RuntimeError("Todos los puntos ya fueron capturados")
        pose = self._robot.get_tcp_pose()
        self._points[step.key] = pose.position
        return self.state()

    def back(self) -> dict:
        if self._points:
            last_key = self.steps[len(self._points) - 1].key
            del self._points[last_key]
        return self.state()

    def reset(self) -> dict:
        self._points.clear()
        return self.state()

    # ------------------------------------------------------------ resultado

    def _tray(self, prefix: str, cols: int, rows: int) -> TrayGrid:
        origin = self._points[f"{prefix}_origin"]
        col_step = _delta(origin, self._points[f"{prefix}_col"])
        if rows > 1:
            row_step = _delta(origin, self._points[f"{prefix}_row"])
        else:
            row_step = Point3(0.0, 0.0, 0.0)
        return TrayGrid(origin=origin, col_step=col_step, row_step=row_step,
                        cols=cols, rows=rows)

    def build(self, robot_host: str) -> CalibrationData:
        if not self.done:
            missing = [s.key for s in self.steps if s.key not in self._points]
            raise RuntimeError(f"Faltan puntos por capturar: {', '.join(missing)}")
        board = BoardGeometry(
            a1=self._points["board_a1"],
            h1=self._points["board_h1"],
            a8=self._points["board_a8"],
            h8=self._points["board_h8"],
        )
        return CalibrationData(
            board=board,
            capture_tray=self._tray("capture", *self._capture_grid),
            reserve_tray=self._tray("reserve", *self._reserve_grid),
            piece_params=(
                dict(self._base.piece_params) if self._base else dict(DEFAULT_PIECE_PARAMS)
            ),
            motion=self._base.motion if self._base else MotionParams(),
            robot_host=robot_host,
        )

    def summary(self) -> dict:
        """Medidas derivadas y advertencias para validar el teach a ojo."""
        p = self._points
        warnings: list[str] = []
        result: dict = {"warnings": warnings}

        if all(f"board_{sq}" in p for sq in ("a1", "h1", "a8", "h8")):
            side_1 = _distance(p["board_a1"], p["board_h1"])  # fila 1
            side_8 = _distance(p["board_a8"], p["board_h8"])  # fila 8
            side_a = _distance(p["board_a1"], p["board_a8"])  # columna a
            side_h = _distance(p["board_h1"], p["board_h8"])  # columna h
            square_mm = (side_1 + side_8 + side_a + side_h) / 4 / 7 * 1000
            result["board"] = {
                "square_size_mm": round(square_mm, 1),
                "sides_mm": {
                    "fila_1": round(side_1 * 1000, 1),
                    "fila_8": round(side_8 * 1000, 1),
                    "col_a": round(side_a * 1000, 1),
                    "col_h": round(side_h * 1000, 1),
                },
            }
            if abs(side_1 - side_8) > 0.005 or abs(side_a - side_h) > 0.005:
                warnings.append(
                    "Los lados opuestos del tablero difieren en más de 5 mm: "
                    "revisá que los puntos sean los centros de las esquinas."
                )
            if not 0.02 <= square_mm / 1000 <= 0.08:
                warnings.append(
                    f"Casilla de {square_mm:.0f} mm: fuera del rango esperado "
                    "(20-80 mm). ¿Se capturó alguna esquina equivocada?"
                )
            z_values = [p[f"board_{sq}"].z for sq in ("a1", "h1", "a8", "h8")]
            if max(z_values) - min(z_values) > 0.005:
                warnings.append(
                    "Las esquinas difieren más de 5 mm en altura: "
                    "¿el TCP tocaba la superficie en todas?"
                )

        for prefix, label in (("capture", "capturas"), ("reserve", "reserva")):
            if f"{prefix}_origin" in p and f"{prefix}_col" in p:
                pitch = _distance(p[f"{prefix}_origin"], p[f"{prefix}_col"]) * 1000
                result[f"{prefix}_pitch_mm"] = round(pitch, 1)
                if not 25 <= pitch <= 120:
                    warnings.append(
                        f"Paso de {pitch:.0f} mm entre slots de la bandeja de "
                        f"{label}: fuera del rango esperado (25-120 mm)."
                    )
        return result
