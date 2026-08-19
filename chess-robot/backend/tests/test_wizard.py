"""Asistente de calibración: captura guiada y construcción de la geometría."""

import pytest

from app.calibration import CalibrationStore
from app.calibration.wizard import CalibrationWizard
from app.robot_controller import Point3, SimulatedRobot
from app.robot_controller.robot import Pose

# Tablero de 350 mm de lado (casilla de 50 mm) apoyado en z=0.02, más dos
# bandejas con slots cada 50 mm. Coordenadas de los puntos del teach:
POINTS = {
    "board_a1": Point3(0.40, -0.175, 0.020),
    "board_h1": Point3(0.75, -0.175, 0.020),
    "board_a8": Point3(0.40, 0.175, 0.020),
    "board_h8": Point3(0.75, 0.175, 0.020),
    "capture_origin": Point3(0.90, -0.30, 0.020),
    "capture_col": Point3(0.95, -0.30, 0.020),
    "capture_row": Point3(0.90, -0.25, 0.020),
    "reserve_origin": Point3(0.90, 0.30, 0.020),
    "reserve_col": Point3(0.95, 0.30, 0.020),
}


def teach_all(wizard: CalibrationWizard, robot: SimulatedRobot) -> None:
    while not wizard.done:
        step = wizard.current_step
        robot.pose = Pose(POINTS[step.key])
        wizard.capture()


def test_steps_reserve_single_row_skips_row_point():
    wizard = CalibrationWizard(SimulatedRobot(), reserve_rows=1)
    keys = [s.key for s in wizard.steps]
    assert keys == [
        "board_a1", "board_h1", "board_a8", "board_h8",
        "capture_origin", "capture_col", "capture_row",
        "reserve_origin", "reserve_col",
    ]


def test_capture_walk_and_build():
    robot = SimulatedRobot()
    wizard = CalibrationWizard(robot)
    teach_all(wizard, robot)

    data = wizard.build(robot_host="192.168.0.25")
    assert data.board.a1 == POINTS["board_a1"]
    assert data.board.h8 == POINTS["board_h8"]

    def approx_point(point: Point3, x: float, y: float, z: float) -> None:
        assert (point.x, point.y, point.z) == pytest.approx((x, y, z))

    # col_step y row_step derivados de los slots vecinos.
    approx_point(data.capture_tray.col_step, 0.05, 0.0, 0.0)
    approx_point(data.capture_tray.row_step, 0.0, 0.05, 0.0)
    # Reserva de una sola fila: sin desplazamiento entre filas.
    approx_point(data.reserve_tray.row_step, 0.0, 0.0, 0.0)
    approx_point(data.reserve_tray.slot_position(1), 0.95, 0.30, 0.020)
    assert data.robot_host == "192.168.0.25"


def test_build_incomplete_fails():
    wizard = CalibrationWizard(SimulatedRobot())
    with pytest.raises(RuntimeError, match="Faltan puntos"):
        wizard.build(robot_host="x")


def test_back_and_recapture():
    robot = SimulatedRobot()
    wizard = CalibrationWizard(robot)
    robot.pose = Pose(Point3(1.0, 1.0, 1.0))  # captura equivocada
    wizard.capture()
    wizard.back()
    assert wizard.current_step.key == "board_a1"
    robot.pose = Pose(POINTS["board_a1"])
    state = wizard.capture()
    assert state["steps"][0]["captured"] == {"x": 0.40, "y": -0.175, "z": 0.020}


def test_summary_reports_square_size_without_warnings():
    robot = SimulatedRobot()
    wizard = CalibrationWizard(robot)
    teach_all(wizard, robot)
    summary = wizard.summary()
    assert summary["board"]["square_size_mm"] == 50.0
    assert summary["capture_pitch_mm"] == 50.0
    assert summary["warnings"] == []


def test_summary_warns_on_inconsistent_corners():
    robot = SimulatedRobot()
    wizard = CalibrationWizard(robot)
    points = dict(POINTS)
    points["board_h8"] = Point3(0.75, 0.155, 0.020)  # 20 mm corrido
    while not wizard.done:
        robot.pose = Pose(points[wizard.current_step.key])
        wizard.capture()
    assert any("lados opuestos" in w.lower() for w in wizard.summary()["warnings"])


def test_roundtrip_through_store(tmp_path):
    robot = SimulatedRobot()
    wizard = CalibrationWizard(robot)
    teach_all(wizard, robot)
    data = wizard.build(robot_host="192.168.0.25")

    store = CalibrationStore(tmp_path / "calibration.json")
    store.save(data)
    loaded = store.load()
    assert loaded.board == data.board
    assert loaded.capture_tray == data.capture_tray
    assert loaded.reserve_tray == data.reserve_tray
