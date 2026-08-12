"""Tests de la API de sensores (driver mock)."""

import chess
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.board_sensor.bitmap import FULL_START_BITMAP


def make_client() -> TestClient:
    # Motor aleatorio: los tests no deben depender de Stockfish ni lanzar
    # procesos pesados (en la Pi, varios Stockfish agotan la RAM).
    import os

    os.environ["CHESS_ENGINE"] = "random"
    return TestClient(create_app(driver_name="mock"))


def test_status_reports_mock_driver():
    with make_client() as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert data["driver"] == "mock"
        assert data["mock"] is True


def test_diagnostics_page_served():
    with make_client() as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Diagnóstico" in response.text


def test_mock_toggle_and_reset():
    with make_client() as client:
        assert client.post("/api/mock/toggle/e2").status_code == 200
        assert client.post("/api/mock/toggle/z9").status_code == 404
        assert client.post("/api/mock/reset").status_code == 200


def wait_for_bitmap(client: TestClient, bitmap: int, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.get("/api/status").json()["bitmap"] == bitmap:
            return
        time.sleep(0.02)
    raise AssertionError("El scanner no estabilizó el bitmap esperado")


def test_full_game_turn_via_api():
    with make_client() as client:
        status = client.post("/api/game/new", json={"human_color": "white"}).json()
        assert status["phase"] == "human_turn"

        # El humano juega e2e4 "físicamente" con los toggles del mock.
        client.post("/api/mock/toggle/e2")
        expected = FULL_START_BITMAP & ~(1 << chess.E2) | (1 << chess.E4)
        client.post("/api/mock/toggle/e4")
        wait_for_bitmap(client, expected)

        status = client.post("/api/game/confirm").json()
        # El robot (simulado) ya respondió y el mundo quedó consistente.
        assert status["phase"] == "human_turn", status
        assert status["san_history"][0] == "e4"
        assert len(status["san_history"]) == 2
        assert status["robot_steps"]


def test_game_state_and_difficulty_endpoints():
    with make_client() as client:
        assert client.get("/api/game/state").json()["phase"] == "idle"
        assert client.post(
            "/api/game/difficulty", json={"level": "avanzado"}
        ).status_code == 200
        assert client.post(
            "/api/game/difficulty", json={"level": "imposible"}
        ).status_code == 422


def test_confirm_without_game_reports_error():
    with make_client() as client:
        status = client.post("/api/game/confirm").json()
        assert status["phase"] == "idle"
        assert status["last_error"] is not None


def test_websocket_streams_initial_and_changes():
    with make_client() as client:
        with client.websocket_connect("/ws/sensors") as ws:
            first = ws.receive_json()
            assert first["bitmap"] == FULL_START_BITMAP
            assert "e2" in first["squares"]
            assert len(first["squares"]) == 32

            client.post("/api/mock/toggle/e2")
            update = ws.receive_json()
            assert update["bitmap"] == FULL_START_BITMAP & ~(1 << chess.E2)
            assert "e2" not in update["squares"]
