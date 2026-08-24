"""Asistente de calibración por teach: esquinas, bandejas y espera.

El operador lleva el TCP (punta de la garra cerrada) a cada punto — con
freedrive o jog — **en cualquier orden**, y lo captura por nombre; también
puede fijar/corregir cualquier punto escribiendo sus coordenadas. Con los
puntos se construye ``CalibrationData``:

- Tablero: centros de a1, h1, a8, h8 tocando la superficie de la casilla.
- Bandejas: slot 0, slot adyacente de la misma fila (define ``col_step``) y
  primer slot de la fila siguiente (define ``row_step``; se omite si la
  bandeja tiene una sola fila).
- ``park`` (opcional): posición de espera del brazo durante el turno humano
  — fuera del tablero, sin tapar la cámara. Si no se enseña, se deriva de
  la bandeja de capturas.

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

    OPTIONAL_KEYS = frozenset({"park"})

    def __init__(
        self,
        robot: RobotInterface,
        capture_cols: int = 4,
        capture_rows: int = 8,
        reserve_cols: int = 2,
        reserve_rows: int = 1,
        base: CalibrationData | None = None,
        prefill: bool = False,
    ) -> None:
        self._robot = robot
        self._capture_grid = (capture_cols, capture_rows)
        self._reserve_grid = (reserve_cols, reserve_rows)
        self._base = base
        self._points: dict[str, Point3] = {}

        steps = [_board_step(sq) for sq in ("a1", "h1", "a8", "h8")]
        steps += self._tray_steps("capture", "bandeja de CAPTURAS", capture_rows)
        steps += self._tray_steps("reserve", "reserva de PROMOCIÓN", reserve_rows)
        steps.append(
            WizardStep(
                key="park",
                title="Posición de espera (opcional)",
                instruction=(
                    "Llevá el brazo a donde debe esperar mientras juega el "
                    "humano: fuera del tablero y sin tapar la cámara."
                ),
            )
        )
        self.steps: list[WizardStep] = steps
        self._keys = {step.key for step in steps}
        if base is not None and prefill:
            self._prefill_from(base)

    def _prefill_from(self, base: CalibrationData) -> None:
        """Carga los puntos desde una calibración guardada, para poder
        corregirlos individualmente sin re-enseñar todo."""
        for corner in ("a1", "h1", "a8", "h8"):
            self._points[f"board_{corner}"] = getattr(base.board, corner)
        for prefix, tray in (
            ("capture", base.capture_tray),
            ("reserve", base.reserve_tray),
        ):
            origin = tray.origin
            self._points[f"{prefix}_origin"] = origin
            self._points[f"{prefix}_col"] = Point3(
                origin.x + tray.col_step.x,
                origin.y + tray.col_step.y,
                origin.z + tray.col_step.z,
            )
            if f"{prefix}_row" in self._keys and tray.rows > 1:
                self._points[f"{prefix}_row"] = Point3(
                    origin.x + tray.row_step.x,
                    origin.y + tray.row_step.y,
                    origin.z + tray.row_step.z,
                )
        if base.park is not None:
            self._points["park"] = base.park

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
        """Primer punto obligatorio que falta (guía; el orden es libre)."""
        for step in self.steps:
            if step.key not in self._points and step.key not in self.OPTIONAL_KEYS:
                return step
        return None

    @property
    def done(self) -> bool:
        return self.current_step is None

    def state(self) -> dict:
        current = self.current_step
        return {
            "steps": [
                {
                    "key": step.key,
                    "title": step.title,
                    "instruction": step.instruction,
                    "optional": step.key in self.OPTIONAL_KEYS,
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
        """Captura secuencial: el siguiente punto obligatorio que falte."""
        step = self.current_step
        if step is None:
            raise RuntimeError("Todos los puntos ya fueron capturados")
        return self.capture_key(step.key)

    def capture_key(self, key: str) -> dict:
        """Captura un punto por nombre desde el TCP actual (orden libre)."""
        self._require_key(key)
        pose = self._robot.get_tcp_pose()
        self._points[key] = pose.position
        return self.state()

    def set_point(self, key: str, point: Point3) -> dict:
        """Fija/corrige un punto escribiendo sus coordenadas (orden libre)."""
        self._require_key(key)
        self._points[key] = point
        return self.state()

    def point(self, key: str) -> Point3:
        self._require_key(key)
        if key not in self._points:
            raise RuntimeError(f"El punto {key!r} todavía no fue definido")
        return self._points[key]

    def _require_key(self, key: str) -> None:
        if key not in self._keys:
            raise ValueError(f"Punto desconocido: {key!r}")

    def back(self) -> dict:
        if self._points:
            last_key = list(self._points)[-1]
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
            park=self._points.get("park"),
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
            if f"{prefix}_origin" in p and f"{prefix}_col" in p and f"{prefix}_row" in p:
                col = _delta(p[f"{prefix}_origin"], p[f"{prefix}_col"])
                row = _delta(p[f"{prefix}_origin"], p[f"{prefix}_row"])
                cross = (
                    (col.y * row.z - col.z * row.y) ** 2
                    + (col.z * row.x - col.x * row.z) ** 2
                    + (col.x * row.y - col.y * row.x) ** 2
                ) ** 0.5
                norms = _distance(Point3(0, 0, 0), col) * _distance(Point3(0, 0, 0), row)
                if norms > 0 and cross / norms < 0.3:
                    warnings.append(
                        f"Bandeja de {label}: la dirección de columnas y la de "
                        "filas son casi paralelas — los slots se superponen. "
                        "El punto 'fila 2' debe ser perpendicular a la fila."
                    )
        return result
