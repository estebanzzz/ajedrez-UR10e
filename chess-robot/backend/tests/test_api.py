"""Tests de la API de sensores (driver mock)."""

import chess
from fastapi.testclient import TestClient

from app.api.server import create_app
from app.board_sensor.bitmap import FULL_START_BITMAP


def make_client() -> TestClient:
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
