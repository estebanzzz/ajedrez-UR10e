import chess

from app.calibration import CalibrationData, CalibrationStore
from app.robot_controller import BoardGeometry, Point3, TrayGrid


def make_data() -> CalibrationData:
    return CalibrationData(
        board=BoardGeometry(
            a1=Point3(0.40, -0.175, 0.02),
            h1=Point3(0.75, -0.175, 0.02),
            a8=Point3(0.40, 0.175, 0.02),
            h8=Point3(0.75, 0.175, 0.021),
        ),
        capture_tray=TrayGrid(
            origin=Point3(0.9, -0.2, 0.02),
            col_step=Point3(0.05, 0, 0),
            row_step=Point3(0, 0.05, 0),
            cols=4,
            rows=8,
        ),
        reserve_tray=TrayGrid(
            origin=Point3(0.9, 0.25, 0.02),
            col_step=Point3(0.05, 0, 0),
            row_step=Point3(0, 0.05, 0),
            cols=2,
            rows=1,
        ),
        robot_host="10.0.0.42",
    )


def test_roundtrip(tmp_path):
    store = CalibrationStore(tmp_path / "calibration.json")
    assert not store.exists()

    original = make_data()
    store.save(original)
    assert store.exists()

    loaded = store.load()
    assert loaded == original


def test_saved_file_is_readable_json(tmp_path):
    import json

    path = tmp_path / "calibration.json"
    CalibrationStore(path).save(make_data())
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["robot_host"] == "10.0.0.42"
    assert data["board"]["a1"]["x"] == 0.40
    assert data["piece_params"]["king"]["height_m"] == 0.095
