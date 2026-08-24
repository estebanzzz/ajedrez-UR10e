"""Tests del menú lateral compartido por las páginas del backend."""

import pytest
from fastapi.testclient import TestClient

from app.api.nav import NAV_ITEMS, with_nav
from app.api.server import create_app


def make_client() -> TestClient:
    import os
    import tempfile

    os.environ["CHESS_ENGINE"] = "random"
    os.environ["CHESS_SCORES_DB"] = os.path.join(tempfile.mkdtemp(), "scores.db")
    return TestClient(create_app(driver_name="mock"))


def test_with_nav_injects_style_and_markup():
    page = "<!doctype html><html><head><title>x</title></head><body><h1>x</h1></body></html>"
    result = with_nav(page, "/")

    assert 'id="sidenav-style"' in result
    assert result.index("sidenav-style") < result.index("</head>")
    assert '<aside id="sidenav">' in result
    # El menú va antes del contenido, no lo reemplaza.
    assert result.index("sidenav") < result.index("<h1>")
    assert "<h1>x</h1>" in result


def test_with_nav_marks_the_active_item():
    page = "<html><head></head><body></body></html>"
    result = with_nav(page, "/calibracion")

    assert '<a href="/calibracion" class="active"' in result
    assert '<a href="/calibration" class="active"' not in result


def test_with_nav_leaves_pages_without_head_or_body_untouched():
    assert with_nav("solo texto", "/") == "solo texto"


@pytest.mark.parametrize("path", ["/", "/calibration", "/calibracion"])
def test_pages_served_with_the_nav(path):
    with make_client() as client:
        response = client.get(path)
        assert response.status_code == 200
        body = response.text
        assert '<aside id="sidenav">' in body
        # Todos los destinos del menú están presentes en cada página.
        for href, _icon, _label, _hint in NAV_ITEMS:
            assert f'href="{href}"' in body
        assert f'<a href="{path}" class="active"' in body
