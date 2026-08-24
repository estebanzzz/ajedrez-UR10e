"""API del backend — sensores (Fase 2) + partida completa (Fase 4).

Uso en desarrollo (driver mock, robot simulado; simular piezas desde la
página de diagnóstico):

    python -m app.api.server

Con hardware real (cámara Basler + UR10e):

    CHESS_DRIVER=vision CHESS_ROBOT_HOST=192.168.1.10 python -m app.api.server

La calibración se lee de ``config/calibration.json`` si existe (si no, se
usan los valores de ejemplo — suficientes para el robot simulado).

Los WebSocket sondean el estado (~20 Hz): scanner y robot corren en hilos
propios y este es el puente más simple y robusto hacia asyncio.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from pathlib import Path

import chess
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.board_sensor import BoardScanner, MockDriver
from app.board_sensor.bitmap import FULL_START_BITMAP, squares_from_bitmap
from app.api.calibration_page import CALIBRATION_HTML
from app.api.diagnostics import DIAGNOSTICS_HTML
from app.api.nav import with_nav
from app.api.vision_page import VISION_CALIBRATION_HTML
from app.calibration import CalibrationStore
from app.calibration.wizard import CalibrationWizard
from app.engine import DIFFICULTY_PRESETS, RandomEngine, StockfishEngine, find_stockfish
from app.game_state import GameState
from app.game_state.orchestrator import GameOrchestrator
from app.personality import VOICE_DIR, Commentator, Event, Heckler, LocalSpeaker
from app.robot_controller import RobotController, SimulatedRobot
from app.scores import ScoreStore
from app.telemetry import MqttBridge

logger = logging.getLogger(__name__)

WS_POLL_INTERVAL = 0.05  # 20 Hz, igual que la spec de escaneo

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _build_driver(name: str):
    if name == "mock":
        return MockDriver(initial=FULL_START_BITMAP)
    if name == "s7":
        from app.board_sensor.s7 import open_s7

        return open_s7(
            host=os.environ.get("CHESS_PLC_HOST", "192.168.0.20"),
            rack=int(os.environ.get("CHESS_PLC_RACK", "0")),
            slot=int(os.environ.get("CHESS_PLC_SLOT", "1")),
            db_in=int(os.environ.get("CHESS_PLC_DB_IN", "1")),
            db_out=int(os.environ.get("CHESS_PLC_DB_OUT", "2")),
        )
    if name == "vision":
        from app.vision import open_vision

        return open_vision(camera_spec=os.environ.get("CHESS_CAMERA", "basler"))
    if name == "matrix":
        from app.board_sensor.matrix_gpio import GpiodBackend, MatrixGPIODriver

        return MatrixGPIODriver(GpiodBackend())
    raise ValueError(
        f"Driver desconocido: {name!r} (opciones: mock, vision, s7, matrix)"
    )


def _load_calibration():
    store = CalibrationStore(CONFIG_DIR / "calibration.json")
    if not store.exists():
        store = CalibrationStore(CONFIG_DIR / "calibration.example.json")
    return store.load()


def _build_engine(difficulty: str):
    if os.environ.get("CHESS_ENGINE") == "random":
        return RandomEngine()
    stockfish_path = find_stockfish()
    if stockfish_path:
        return StockfishEngine(stockfish_path, difficulty=difficulty)
    return RandomEngine()


class CornersRequest(BaseModel):
    corners: list[list[float]]  # 4 puntos [x, y], orden a1, h1, h8, a8
    warp_size: int = 512


class NewGameRequest(BaseModel):
    mode: str = "human"  # "human" | "self_play" (el robot juega contra sí mismo)
    human_color: str = "white"  # "white" | "black"
    player_name: str = ""
    # Contacto para el premio: se guarda en la base y NO se muestra en la UI
    # ni se devuelve por ningún endpoint.
    player_email: str = ""
    difficulty: str | None = None
    # Minutos del reloj del humano (solo corre en su turno). None = default
    # del kiosk (CHESS_ROBOT_GAME_MINUTES, 5). 0 = partida sin reloj.
    time_minutes: float | None = None
    # Personalidad/voz del robot para esta partida (ids de voice_config.json).
    personality: str | None = None


class ChooseRequest(BaseModel):
    move: str  # UCI de la jugada elegida entre las pending_choices


class SpeechLevelRequest(BaseModel):
    level: int


class SpeechPersonalityRequest(BaseModel):
    personality: str


class SpeechTestRequest(BaseModel):
    event: str = "filler"


class DifficultyRequest(BaseModel):
    level: str


class FreedriveRequest(BaseModel):
    enabled: bool


class GripperRequest(BaseModel):
    opening_mm: float
    force: float = 0.25


class JogRequest(BaseModel):
    axis: str  # "x" | "y" | "z"
    delta_mm: float  # positivo/negativo; se limita a ±50 mm por paso


class PointRequest(BaseModel):
    key: str
    x_mm: float
    y_mm: float
    z_mm: float


class PointKeyRequest(BaseModel):
    key: str


class GotoPointRequest(BaseModel):
    key: str
    clearance_mm: float = 40.0


class MotionRequest(BaseModel):
    """Campos ajustables de movimiento y garra (los demás quedan como están).

    ``grip_opening_mm`` (cierre sobre la pieza) y ``grip_force`` aplican de
    forma uniforme a todos los tipos de pieza (fichas iguales).
    """

    grip_settle_s: float | None = None
    max_opening_mm: float | None = None
    grip_opening_mm: float | None = None
    grip_force: float | None = None
    speed_travel: float | None = None
    speed_vertical: float | None = None


_MOTION_RANGES = {
    "grip_settle_s": (0.0, 3.0),
    "max_opening_mm": (10.0, 50.0),
    "grip_opening_mm": (0.0, 50.0),
    "grip_force": (0.05, 1.0),
    "speed_travel": (0.02, 0.5),
    "speed_vertical": (0.02, 0.25),
}
_GRIPPER_FIELDS = {"grip_opening_mm", "grip_force"}


class GotoSquareRequest(BaseModel):
    square: str
    clearance_mm: float = 80.0


def create_app(driver_name: str | None = None) -> FastAPI:
    driver_name = driver_name or os.environ.get("CHESS_DRIVER", "mock")
    driver = _build_driver(driver_name)
    # Visión: cada read() procesa un frame completo; 10 Hz sobra y alivia la Pi.
    scanner = BoardScanner(driver, scan_hz=10.0 if driver_name == "vision" else 30.0)
    if driver_name == "vision":
        from app.vision import tuning as vision_tuning

        scanner.set_stable_reads(vision_tuning.TUNING.stable_reads)

    calibration = _load_calibration()
    robot_host = os.environ.get("CHESS_ROBOT_HOST")
    if robot_host:
        # Proxy con reintentos: aunque el backend arranque antes que el UR
        # (boot lento, corte), la conexión se establece sola cuando aparece.
        from app.robot_controller.robot import LazyURRobot

        robot_arm = LazyURRobot(robot_host)
    else:
        robot_arm = SimulatedRobot()
    controller = RobotController(
        robot_arm,
        calibration.board,
        calibration.capture_tray,
        calibration.reserve_tray,
        calibration.piece_params,
        calibration.motion,
        park_position=calibration.park,
    )
    engine = _build_engine(os.environ.get("CHESS_DIFFICULTY", "intermedio"))
    game = GameState()
    scores = ScoreStore(
        os.environ.get("CHESS_SCORES_DB", CONFIG_DIR.parent / "data" / "scores.db")
    )
    # Sin robot real: el mundo simulado se actualiza solo tras cada jugada.
    on_robot_moved = driver.set_bitmap if (
        isinstance(driver, MockDriver) and robot_host is None
    ) else None
    # Voz del robot: frases + audios pregenerados (scripts/build_voice.py).
    # Con CHESS_ROBOT_SPEECH_PLAYER (p. ej. "mpg123 -q") el audio sale por el
    # parlante de la Pi; si no, lo reproduce el navegador del kiosk.
    commentator = Commentator(level=int(os.environ.get("CHESS_ROBOT_VOICE_LEVEL", "2")))
    if os.environ.get("CHESS_ROBOT_PERSONALITY"):
        commentator.set_personality(os.environ["CHESS_ROBOT_PERSONALITY"])
    speech_player = os.environ.get("CHESS_ROBOT_SPEECH_PLAYER")
    speaker = LocalSpeaker(speech_player) if speech_player else None
    if speaker is not None:
        commentator.subscribe(speaker)
        commentator.local_playback = True
    orchestrator = GameOrchestrator(
        game, scanner, engine, controller, on_robot_moved, scores=scores,
        commentator=commentator,
    )
    heckler = Heckler(commentator, orchestrator.status)

    # Telemetría hacia Neuronal HUB (supervisión de la feria): publica los
    # valores numéricos de la partida por MQTT. Apagado sin CHESS_MQTT_HOST.
    mqtt_host = os.environ.get("CHESS_MQTT_HOST")
    mqtt_bridge = (
        MqttBridge(
            orchestrator.status,
            host=mqtt_host,
            port=int(os.environ.get("CHESS_MQTT_PORT", "1883")),
            username=os.environ.get("CHESS_MQTT_USERNAME"),
            password=os.environ.get("CHESS_MQTT_PASSWORD"),
        )
        if mqtt_host
        else None
    )

    # Con PLC: botón físico de confirmación y baliza según fase de la partida.
    from app.board_sensor.s7 import PanelLink, S7Driver

    panel_link = (
        PanelLink(
            driver,
            get_phase=lambda: orchestrator.phase.value,
            on_button=orchestrator.confirm,
        )
        if isinstance(driver, S7Driver)
        else None
    )

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        scanner.start()
        if panel_link is not None:
            panel_link.start()
        heckler.start()
        if mqtt_bridge is not None:
            mqtt_bridge.start()
        yield
        if mqtt_bridge is not None:
            mqtt_bridge.stop()
        heckler.stop()
        if speaker is not None:
            speaker.close()
        if panel_link is not None:
            panel_link.stop()
        scanner.stop()
        driver.close()
        engine.close()
        robot_arm.close()
        scores.close()

    app = FastAPI(title="Chess Robot", lifespan=lifespan)
    # El sistema de supervisión de la feria (dashboards en otra máquina de la
    # LAN) consume /api/stats/* desde el navegador: CORS solo lectura.
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"]
    )
    app.state.scanner = scanner
    app.state.driver = driver
    app.state.driver_name = driver_name
    app.state.orchestrator = orchestrator
    app.state.commentator = commentator
    if VOICE_DIR.is_dir():
        app.mount("/voice", StaticFiles(directory=VOICE_DIR), name="voice")

    def _sensor_message() -> dict:
        bitmap = scanner.latest
        return {
            "bitmap": bitmap,
            "squares": [chess.SQUARE_NAMES[s] for s in squares_from_bitmap(bitmap or 0)],
        }

    # ------------------------------------------------------------------ REST

    @app.get("/", response_class=HTMLResponse)
    def diagnostics_page() -> str:
        return with_nav(DIAGNOSTICS_HTML, "/")

    @app.get("/api/status")
    def status() -> dict:
        result = {
            "driver": driver_name,
            "mock": isinstance(driver, MockDriver),
            "read_errors": scanner.read_errors,
            **_sensor_message(),
        }
        if driver_name == "vision":
            from app.vision import tuning as vision_tuning

            result["vision"] = {
                "occluded": driver.occluded,
                "anomalous_cells": driver.anomalous_cells,
                "anomalous_squares": [
                    chess.SQUARE_NAMES[s] for s in driver.anomalous_squares
                ],
                "motion": round(driver.motion, 2),
                "motion_threshold": vision_tuning.TUNING.motion_threshold,
                "implausible": driver.implausible,
            }
            if not driver.model_ready:
                result["vision"]["warning"] = (
                    "Sin modelo de visión entrenado (o quedó obsoleto tras "
                    "actualizar el clasificador): entrenar en /calibracion "
                    "— tablero vacío + posición inicial"
                )
            elif driver.implausible:
                result["vision"]["warning"] = (
                    "Lectura imposible (>32 casillas ocupadas): el modelo no "
                    "corresponde a la iluminación/exposición actual — "
                    "re-entrenar en /calibracion"
                )
        if isinstance(driver, S7Driver):
            result["plc"] = {
                "alive": driver.plc_alive(),
                "heartbeat": driver.heartbeat,
                "estop_ok": driver.estop_ok,
                "button": driver.button_pressed,
            }
        return result

    def _to_jpeg(frame) -> bytes | None:
        if frame is None:
            return None
        import cv2

        ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return jpg.tobytes() if ok else None

    def _vision_debug_jpeg() -> bytes | None:
        return _to_jpeg(driver.debug_frame())

    def _require_vision() -> None:
        if driver_name != "vision":
            raise HTTPException(400, "Solo disponible con el driver vision")

    def _mjpeg_stream(get_jpeg, interval: float) -> StreamingResponse:
        async def frames():
            while True:
                jpg = get_jpeg()
                if jpg is not None:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                    )
                await asyncio.sleep(interval)

        return StreamingResponse(
            frames(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/api/vision/frame")
    def vision_frame() -> Response:
        """Snapshot JPEG de lo que ve el clasificador (depuración)."""
        _require_vision()
        jpg = _vision_debug_jpeg()
        if jpg is None:
            raise HTTPException(503, "Sin frame de cámara todavía")
        return Response(jpg, media_type="image/jpeg")

    @app.get("/api/vision/stream")
    async def vision_stream() -> StreamingResponse:
        """Stream MJPEG en vivo de la vista del clasificador (depuración)."""
        _require_vision()
        return _mjpeg_stream(_vision_debug_jpeg, interval=0.2)  # ~5 fps

    @app.get("/api/vision/raw-stream")
    async def vision_raw_stream() -> StreamingResponse:
        """Stream MJPEG del frame crudo (página de calibración)."""
        _require_vision()
        return _mjpeg_stream(lambda: _to_jpeg(driver.raw_frame()), interval=0.35)

    @app.get("/api/vision/tuning")
    def vision_tuning_get() -> dict:
        """Parámetros ajustables de la detección (con rangos válidos)."""
        _require_vision()
        from app.vision import tuning

        return {
            "values": tuning.to_dict(),
            "ranges": tuning.RANGES,
            "retrain_fields": sorted(tuning.RETRAIN_FIELDS),
        }

    @app.post("/api/vision/tuning")
    def vision_tuning_set(values: dict) -> dict:
        """Aplica cambios de parámetros en vivo y los persiste."""
        _require_vision()
        from app.vision import tuning

        try:
            changed = tuning.update(values)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if "stable_reads" in changed:
            scanner.set_stable_reads(tuning.TUNING.stable_reads)
        if changed:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tuning.save(CONFIG_DIR / tuning.TUNING_FILE)
        return {
            "values": tuning.to_dict(),
            "changed": sorted(changed),
            "retrain_required": bool(changed & tuning.RETRAIN_FIELDS),
        }

    # ------------------------------------------ calibración de visión por web

    # Sesión de entrenamiento de la página /calibracion.
    vision_train: dict = {"trainer": None}

    def _vision_trainer():
        from app.vision.classifier import Trainer

        if vision_train["trainer"] is None:
            vision_train["trainer"] = Trainer(driver.geometry)
        return vision_train["trainer"]

    @app.post("/api/vision/corners")
    def vision_set_corners(req: CornersRequest) -> dict:
        """Guarda las 4 esquinas del tablero y las aplica en caliente."""
        _require_vision()
        if len(req.corners) != 4:
            raise HTTPException(400, "Se requieren exactamente 4 esquinas")
        from app.vision.driver import GEOMETRY_FILE
        from app.vision.geometry import BoardGeometry

        geometry = BoardGeometry(corners=req.corners, warp_size=req.warp_size)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        geometry.save(CONFIG_DIR / GEOMETRY_FILE)
        driver.set_geometry(geometry)
        vision_train["trainer"] = None  # las muestras viejas ya no valen
        return {"saved": True}

    @app.post("/api/vision/train/{phase_name}")
    async def vision_train_capture(phase_name: str) -> dict:
        """Captura una tanda de frames para el entrenamiento auto-etiquetado.

        ``empty`` = tablero vacío, ``start`` = posición inicial, ``reset``
        descarta la sesión. Cuando hay al menos una tanda de cada tipo, el
        modelo se entrena, se guarda y se aplica en caliente.
        """
        _require_vision()
        if phase_name == "reset":
            vision_train["trainer"] = None
            return {"empty_frames": 0, "start_frames": 0, "trained": False}
        if phase_name not in ("empty", "start"):
            raise HTTPException(400, "Fase desconocida (empty | start | reset)")

        trainer = _vision_trainer()
        add = (
            trainer.add_empty_frame if phase_name == "empty" else trainer.add_start_frame
        )
        for _ in range(8):
            raw = driver.raw_frame()
            if raw is None:
                raise HTTPException(503, "Sin frame de cámara todavía")
            add(raw)
            await asyncio.sleep(0.15)

        empties, starts = trainer.counts
        result = {"empty_frames": empties, "start_frames": starts, "trained": False}
        if empties and starts:
            from app.vision.driver import MODEL_FILE

            model = trainer.train()
            model.save(CONFIG_DIR / MODEL_FILE)
            driver.set_model(model)
            result.update(trained=True, stats=model.stats)
        return result

    @app.post("/api/mock/toggle/{square_name}")
    def mock_toggle(square_name: str) -> dict:
        if not isinstance(driver, MockDriver):
            raise HTTPException(400, "Solo disponible con el driver mock")
        try:
            square = chess.SQUARE_NAMES.index(square_name.lower())
        except ValueError:
            raise HTTPException(404, f"Casilla inválida: {square_name}")
        driver.toggle_square(square)
        return {"square": square_name.lower()}

    @app.post("/api/mock/reset")
    def mock_reset() -> dict:
        if not isinstance(driver, MockDriver):
            raise HTTPException(400, "Solo disponible con el driver mock")
        driver.set_bitmap(FULL_START_BITMAP)
        return {"bitmap": FULL_START_BITMAP}

    # ------------------------------------------------- robot y calibración

    wizard_holder: dict[str, CalibrationWizard | None] = {"wizard": None}
    calibration_path = CONFIG_DIR / "calibration.json"

    @app.get("/calibracion", response_class=HTMLResponse)
    def vision_calibration_page() -> str:
        """Calibración de visión: esquinas del tablero + entrenamiento."""
        return with_nav(VISION_CALIBRATION_HTML, "/calibracion")

    @app.get("/calibration", response_class=HTMLResponse)
    def calibration_page() -> str:
        return with_nav(CALIBRATION_HTML, "/calibration")

    @app.get("/api/robot/status")
    def robot_status() -> dict:
        return robot_arm.status()

    JOG_MAX_STEP_MM = 50.0
    JOG_SPEED = 0.02  # m/s — lento, para ajuste fino de calibración
    JOG_ACCEL = 0.15

    @app.post("/api/robot/jog")
    def robot_jog(request: JogRequest) -> dict:
        """Mueve el TCP un paso relativo sobre un eje (ajuste fino de teach).

        Alternativa precisa al freedrive: pasos acotados a ±50 mm, moveL
        lento y orientación de la herramienta sin cambios.
        """
        axis = request.axis.lower()
        if axis not in ("x", "y", "z"):
            raise HTTPException(400, "Eje inválido (x | y | z)")
        delta_m = max(-JOG_MAX_STEP_MM, min(JOG_MAX_STEP_MM, request.delta_mm)) / 1000.0
        try:
            from app.robot_controller.geometry import Point3
            from app.robot_controller.robot import Pose

            pose = robot_arm.get_tcp_pose()
            p = pose.position
            target = Point3(
                p.x + (delta_m if axis == "x" else 0.0),
                p.y + (delta_m if axis == "y" else 0.0),
                p.z + (delta_m if axis == "z" else 0.0),
            )
            robot_arm.move_linear(
                Pose(target, pose.rx, pose.ry, pose.rz), JOG_SPEED, JOG_ACCEL
            )
            new_pose = robot_arm.get_tcp_pose()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        np = new_pose.position
        return {"tcp_mm": [round(np.x * 1000, 1), round(np.y * 1000, 1), round(np.z * 1000, 1)]}

    @app.post("/api/robot/freedrive")
    def robot_freedrive(request: FreedriveRequest) -> dict:
        try:
            robot_arm.set_freedrive(request.enabled)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {"freedrive": request.enabled}

    @app.post("/api/robot/gripper")
    def robot_gripper(request: GripperRequest) -> dict:
        if not 0 <= request.opening_mm <= 50:
            raise HTTPException(422, "opening_mm debe estar entre 0 y 50")
        robot_arm.gripper_move(request.opening_mm, request.force)
        return {"opening_mm": request.opening_mm}

    def _motion_values() -> dict:
        from dataclasses import asdict

        # Cierre/fuerza: uniformes para todas las piezas — se muestra el valor
        # del peón como representativo.
        pawn = controller.piece_params[chess.PAWN]
        return {
            **asdict(controller.motion),
            "grip_opening_mm": pawn.grip_opening_mm,
            "grip_force": pawn.grip_force,
        }

    @app.get("/api/robot/motion")
    def robot_motion_get() -> dict:
        return {"values": _motion_values(), "ranges": _MOTION_RANGES}

    @app.post("/api/robot/motion")
    def robot_motion_set(request: MotionRequest) -> dict:
        """Aplica parámetros de movimiento/garra en caliente y los persiste."""
        from dataclasses import replace

        changes = {k: v for k, v in request.model_dump().items() if v is not None}
        for key, value in changes.items():
            lo, hi = _MOTION_RANGES[key]
            if not lo <= value <= hi:
                raise HTTPException(
                    422, f"{key}: valor {value} fuera de rango [{lo}, {hi}]"
                )
        gripper_changes = {k: v for k, v in changes.items() if k in _GRIPPER_FIELDS}
        motion_changes = {k: v for k, v in changes.items() if k not in _GRIPPER_FIELDS}
        if motion_changes:
            controller.set_motion(replace(controller.motion, **motion_changes))
        if gripper_changes:
            controller.set_gripper_params(**gripper_changes)
        # Persistir dentro de calibration.json (motion + piece_params).
        data = _load_calibration()
        data.motion = controller.motion
        data.piece_params = controller.piece_params
        CalibrationStore(calibration_path).save(data)
        return {"values": _motion_values(), "changed": sorted(changes)}

    def _wizard() -> CalibrationWizard:
        wizard = wizard_holder["wizard"]
        if wizard is None:
            raise HTTPException(409, "No hay sesión de calibración: iniciá una")
        return wizard

    @app.post("/api/calibration/start")
    def calibration_start() -> dict:
        base = None
        store = CalibrationStore(calibration_path)
        if store.exists():
            base = store.load()
        # prefill: la tabla arranca con la calibración vigente y el operador
        # corrige solo los puntos que quiera, en cualquier orden.
        wizard_holder["wizard"] = CalibrationWizard(robot_arm, base=base, prefill=True)
        return wizard_holder["wizard"].state()

    @app.post("/api/calibration/point")
    def calibration_set_point(request: PointRequest) -> dict:
        """Fija un punto escribiendo sus coordenadas (mm), en orden libre."""
        from app.robot_controller.geometry import Point3

        try:
            return _wizard().set_point(
                request.key,
                Point3(request.x_mm / 1000, request.y_mm / 1000, request.z_mm / 1000),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/api/calibration/capture-point")
    def calibration_capture_point(request: PointKeyRequest) -> dict:
        """Captura un punto por nombre desde el TCP actual, en orden libre."""
        try:
            return _wizard().capture_key(request.key)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/calibration/goto-point")
    def calibration_goto_point(request: GotoPointRequest) -> dict:
        """Mueve el TCP (lento) sobre un punto calibrado, para verificarlo."""
        from app.robot_controller.robot import Pose
        from app.robot_controller.geometry import Point3

        clearance = max(10.0, min(300.0, request.clearance_mm)) / 1000
        try:
            p = _wizard().point(request.key)
            robot_arm.move_linear(
                Pose(Point3(p.x, p.y, p.z + clearance)), 0.05, 0.2
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {"key": request.key, "clearance_mm": clearance * 1000}

    @app.get("/api/calibration/state")
    def calibration_state() -> dict:
        return _wizard().state()

    @app.post("/api/calibration/capture")
    def calibration_capture() -> dict:
        try:
            return _wizard().capture()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/api/calibration/back")
    def calibration_back() -> dict:
        return _wizard().back()

    @app.post("/api/calibration/save")
    def calibration_save() -> dict:
        wizard = _wizard()
        current_host = robot_host or _load_calibration().robot_host
        try:
            data = wizard.build(robot_host=current_host)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        CalibrationStore(calibration_path).save(data)
        return {
            "path": str(calibration_path),
            "summary": wizard.summary(),
            "note": (
                "La partida usa la calibración cargada al iniciar el servidor: "
                "reiniciarlo para aplicar la nueva."
            ),
        }

    @app.post("/api/calibration/goto")
    def calibration_goto(request: GotoSquareRequest) -> dict:
        """Prueba: mueve el TCP (lento) a la altura segura sobre una casilla."""
        from app.robot_controller.robot import Pose

        store = CalibrationStore(calibration_path)
        if not store.exists():
            raise HTTPException(409, "No hay calibración guardada todavía")
        try:
            square = chess.SQUARE_NAMES.index(request.square.lower())
        except ValueError:
            raise HTTPException(422, f"Casilla inválida: {request.square}")
        if not 20.0 <= request.clearance_mm <= 300.0:
            raise HTTPException(422, "clearance_mm debe estar entre 20 y 300")

        center = store.load().board.square_center(square)
        target_z = center.z + request.clearance_mm / 1000.0
        speed, accel = 0.10, 0.3  # lento: es un movimiento de verificación

        try:
            current = robot_arm.get_tcp_pose()
            travel_z = max(target_z, current.position.z)
            # Subir, trasladar en horizontal y recién entonces descender.
            robot_arm.move_linear(current.at_height(travel_z), speed, accel)
            target = Pose(center, current.rx, current.ry, current.rz)
            robot_arm.move_linear(target.at_height(travel_z), speed, accel)
            robot_arm.move_linear(target.at_height(target_z), speed, accel)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        return {
            "square": request.square.lower(),
            "target": {"x": center.x, "y": center.y, "z": target_z},
        }

    # -------------------------------------------------------------- partida

    @app.get("/api/game/state")
    def game_state() -> dict:
        return orchestrator.status()

    @app.post("/api/game/new")
    def game_new(request: NewGameRequest) -> dict:
        if request.mode not in ("human", "self_play"):
            raise HTTPException(422, "mode debe ser 'human' o 'self_play'")
        if request.human_color not in ("white", "black"):
            raise HTTPException(422, "human_color debe ser 'white' o 'black'")
        if request.difficulty is not None:
            if request.difficulty not in DIFFICULTY_PRESETS:
                raise HTTPException(422, f"Niveles: {', '.join(DIFFICULTY_PRESETS)}")
            engine.set_difficulty(request.difficulty)
        if request.personality is not None:
            try:
                commentator.set_personality(request.personality)
            except ValueError as err:
                raise HTTPException(422, str(err)) from None
        minutes = request.time_minutes
        if minutes is None:
            minutes = float(os.environ.get("CHESS_ROBOT_GAME_MINUTES", "5"))
        if not 0 <= minutes <= 180:
            raise HTTPException(422, "time_minutes: entre 0 (sin reloj) y 180")
        color = chess.WHITE if request.human_color == "white" else chess.BLACK
        orchestrator.new_game(
            human_color=color,
            player_name=request.player_name,
            player_email=request.player_email,
            self_play=request.mode == "self_play",
            time_limit_s=None if minutes == 0 else minutes * 60,
        )
        return orchestrator.status()

    @app.post("/api/game/stop")
    def game_stop() -> dict:
        """Detiene la partida en curso (fin de la demo robot vs robot)."""
        return orchestrator.stop_game()

    @app.post("/api/game/resign")
    def game_resign() -> dict:
        """El humano abandona: derrota con puntaje (botón de la pantalla)."""
        return orchestrator.resign()

    @app.get("/api/ranking")
    def ranking(limit: int = 10) -> dict:
        return {
            "today": scores.top_today(limit=limit),
            "alltime": scores.top_alltime(limit=limit),
        }

    # ------------------------------------- estadísticas (supervisión/feria)

    @app.get("/api/stats/summary")
    def stats_summary(days: int = 7) -> dict:
        """Agregados de los últimos ``days`` días para los dashboards."""
        return scores.stats_summary(days=max(1, min(days, 365)))

    @app.get("/api/stats/games")
    def stats_games(
        limit: int = 50, offset: int = 0, day: str | None = None,
        mode: str | None = None,
    ) -> dict:
        """Listado paginado de partidas (sin SAN). Filtros: day, mode."""
        if mode is not None and mode not in ("human", "self_play"):
            raise HTTPException(422, "mode debe ser 'human' o 'self_play'")
        return scores.list_games(
            limit=max(1, min(limit, 500)), offset=max(0, offset), day=day, mode=mode,
        )

    @app.get("/api/stats/games/{game_id}")
    def stats_game_detail(game_id: int) -> dict:
        """Partida completa, con las jugadas SAN y el FEN final."""
        game = scores.game_detail(game_id)
        if game is None:
            raise HTTPException(404, "Partida no encontrada")
        return game

    @app.post("/api/game/confirm")
    def game_confirm() -> dict:
        """Botón de confirmación de jugada del humano."""
        return orchestrator.confirm()

    @app.post("/api/game/choose")
    def game_choose(request: ChooseRequest) -> dict:
        """El humano elige entre capturas indistinguibles para la cámara."""
        return orchestrator.choose(request.move)

    @app.post("/api/game/pause")
    def game_pause() -> dict:
        """Pausa de emergencia: reloj detenido y jugadas bloqueadas."""
        return orchestrator.pause()

    @app.post("/api/game/resume")
    def game_resume() -> dict:
        """Reanuda la partida pausada."""
        return orchestrator.resume()

    @app.post("/api/game/resync-check")
    def game_resync_check() -> dict:
        resumed = orchestrator.resync_check()
        return {"resumed": resumed, **orchestrator.status()}

    @app.post("/api/game/difficulty")
    def game_difficulty(request: DifficultyRequest) -> dict:
        if request.level not in DIFFICULTY_PRESETS:
            raise HTTPException(422, f"Niveles: {', '.join(DIFFICULTY_PRESETS)}")
        engine.set_difficulty(request.level)
        return {"level": request.level}

    # ------------------------------------------------------------------ voz

    @app.get("/api/speech")
    def speech_state() -> dict:
        return commentator.status()

    @app.post("/api/speech/level")
    def speech_level(request: SpeechLevelRequest) -> dict:
        """0 = mudo · 1 = solo comenta la partida · 2 = provoca y distrae."""
        try:
            commentator.set_level(request.level)
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        return commentator.status()

    @app.post("/api/speech/personality")
    def speech_personality(request: SpeechPersonalityRequest) -> dict:
        """Cambia la personalidad/voz del robot (ids en voice_config.json)."""
        try:
            commentator.set_personality(request.personality)
        except ValueError as err:
            raise HTTPException(422, str(err)) from None
        return commentator.status()

    @app.post("/api/speech/test")
    def speech_test(request: SpeechTestRequest) -> dict:
        """Dice una frase de la categoría pedida (prueba de parlante)."""
        try:
            event = Event(request.event)
        except ValueError:
            raise HTTPException(422, f"Eventos: {', '.join(e.value for e in Event)}") from None
        commentator.say(event, force=True)
        return commentator.status()

    # ------------------------------------------------------------- WebSocket

    @app.websocket("/ws/game")
    async def game_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sent: dict | None = None
        try:
            while True:
                status = orchestrator.status()
                if status != last_sent:
                    await websocket.send_json(status)
                    last_sent = status
                await asyncio.sleep(WS_POLL_INTERVAL)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws/sensors")
    async def sensors_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        last_sent: int | None = None
        try:
            while True:
                bitmap = scanner.latest
                if bitmap is not None and bitmap != last_sent:
                    await websocket.send_json(_sensor_message())
                    last_sent = bitmap
                await asyncio.sleep(WS_POLL_INTERVAL)
        except WebSocketDisconnect:
            pass

    # -------------------------------------------------- UI de exposición (/ui)

    if FRONTEND_DIST.is_dir():
        app.mount("/ui", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # Los INFO de la app (MQTT, jugadas del robot) deben verse en el journal;
    # uvicorn solo configura sus propios loggers.
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s"
    )
    uvicorn.run(app, host="0.0.0.0", port=8000)
