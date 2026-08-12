"""API del backend — sensores (Fase 2) + partida completa (Fase 4).

Uso en desarrollo (driver mock, robot simulado; simular piezas desde la
página de diagnóstico):

    python -m app.api.server

En la Pi con hardware real:

    CHESS_DRIVER=matrix CHESS_ROBOT_HOST=192.168.1.10 python -m app.api.server

La calibración se lee de ``config/calibration.json`` si existe (si no, se
usan los valores de ejemplo — suficientes para el robot simulado).

Los WebSocket sondean el estado (~20 Hz): scanner y robot corren en hilos
propios y este es el puente más simple y robusto hacia asyncio.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import chess
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.board_sensor import BoardScanner, MockDriver
from app.board_sensor.bitmap import FULL_START_BITMAP, squares_from_bitmap
from app.api.diagnostics import DIAGNOSTICS_HTML
from app.calibration import CalibrationStore
from app.engine import DIFFICULTY_PRESETS, RandomEngine, StockfishEngine, find_stockfish
from app.game_state import GameState
from app.game_state.orchestrator import GameOrchestrator
from app.robot_controller import RobotController, SimulatedRobot

WS_POLL_INTERVAL = 0.05  # 20 Hz, igual que la spec de escaneo

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _build_driver(name: str):
    if name == "mock":
        return MockDriver(initial=FULL_START_BITMAP)
    if name == "matrix":
        from app.board_sensor.matrix_gpio import GpiodBackend, MatrixGPIODriver

        return MatrixGPIODriver(GpiodBackend())
    raise ValueError(f"Driver desconocido: {name!r} (opciones: mock, matrix)")


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


class NewGameRequest(BaseModel):
    human_color: str = "white"  # "white" | "black"


class DifficultyRequest(BaseModel):
    level: str


def create_app(driver_name: str | None = None) -> FastAPI:
    driver_name = driver_name or os.environ.get("CHESS_DRIVER", "mock")
    driver = _build_driver(driver_name)
    scanner = BoardScanner(driver)

    calibration = _load_calibration()
    robot_host = os.environ.get("CHESS_ROBOT_HOST")
    if robot_host:
        from app.robot_controller.robot import URRtdeRobot

        robot_arm = URRtdeRobot(robot_host)
    else:
        robot_arm = SimulatedRobot()
    controller = RobotController(
        robot_arm,
        calibration.board,
        calibration.capture_tray,
        calibration.reserve_tray,
        calibration.piece_params,
        calibration.motion,
    )
    engine = _build_engine(os.environ.get("CHESS_DIFFICULTY", "intermedio"))
    game = GameState()
    # Sin robot real: el mundo simulado se actualiza solo tras cada jugada.
    on_robot_moved = driver.set_bitmap if (
        isinstance(driver, MockDriver) and robot_host is None
    ) else None
    orchestrator = GameOrchestrator(game, scanner, engine, controller, on_robot_moved)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        scanner.start()
        yield
        scanner.stop()
        driver.close()
        engine.close()
        robot_arm.close()

    app = FastAPI(title="Chess Robot", lifespan=lifespan)
    app.state.scanner = scanner
    app.state.driver = driver
    app.state.driver_name = driver_name
    app.state.orchestrator = orchestrator

    def _sensor_message() -> dict:
        bitmap = scanner.latest
        return {
            "bitmap": bitmap,
            "squares": [chess.SQUARE_NAMES[s] for s in squares_from_bitmap(bitmap or 0)],
        }

    # ------------------------------------------------------------------ REST

    @app.get("/", response_class=HTMLResponse)
    def diagnostics_page() -> str:
        return DIAGNOSTICS_HTML

    @app.get("/api/status")
    def status() -> dict:
        return {
            "driver": driver_name,
            "mock": isinstance(driver, MockDriver),
            "read_errors": scanner.read_errors,
            **_sensor_message(),
        }

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

    # -------------------------------------------------------------- partida

    @app.get("/api/game/state")
    def game_state() -> dict:
        return orchestrator.status()

    @app.post("/api/game/new")
    def game_new(request: NewGameRequest) -> dict:
        if request.human_color not in ("white", "black"):
            raise HTTPException(422, "human_color debe ser 'white' o 'black'")
        color = chess.WHITE if request.human_color == "white" else chess.BLACK
        orchestrator.new_game(human_color=color)
        return orchestrator.status()

    @app.post("/api/game/confirm")
    def game_confirm() -> dict:
        """Botón de confirmación de jugada del humano."""
        return orchestrator.confirm()

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

    uvicorn.run(app, host="0.0.0.0", port=8000)
