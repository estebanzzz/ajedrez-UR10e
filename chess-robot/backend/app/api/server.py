"""API del backend — Fase 2: estado y streaming de sensores + diagnóstico.

Uso en desarrollo (driver mock, simular piezas desde la página de diagnóstico):

    python -m app.api.server

En la Pi con hardware real (matriz en los GPIO):

    CHESS_DRIVER=matrix python -m app.api.server

Los WebSocket sondean ``scanner.latest`` (~20 Hz): el scanner corre en su
propio hilo y este es el puente más simple y robusto hacia asyncio.
"""

from __future__ import annotations

import asyncio
import contextlib
import os

import chess
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from app.board_sensor import BoardScanner, MockDriver
from app.board_sensor.bitmap import FULL_START_BITMAP, squares_from_bitmap
from app.api.diagnostics import DIAGNOSTICS_HTML

WS_POLL_INTERVAL = 0.05  # 20 Hz, igual que la spec de escaneo


def _build_driver(name: str):
    if name == "mock":
        return MockDriver(initial=FULL_START_BITMAP)
    if name == "matrix":
        from app.board_sensor.matrix_gpio import GpiodBackend, MatrixGPIODriver

        return MatrixGPIODriver(GpiodBackend())
    raise ValueError(f"Driver desconocido: {name!r} (opciones: mock, matrix)")


def create_app(driver_name: str | None = None) -> FastAPI:
    driver_name = driver_name or os.environ.get("CHESS_DRIVER", "mock")
    driver = _build_driver(driver_name)
    scanner = BoardScanner(driver)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        scanner.start()
        yield
        scanner.stop()
        driver.close()

    app = FastAPI(title="Chess Robot — sensores", lifespan=lifespan)
    app.state.scanner = scanner
    app.state.driver = driver
    app.state.driver_name = driver_name

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

    # ------------------------------------------------------------- WebSocket

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

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
