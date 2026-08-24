"""Arranque del backend con el driver de visión.

Equivalente a ``CHESS_DRIVER=vision python -m app.api.server``; existe para
poder lanzarlo sin depender de variables de entorno del shell (lo usa
.claude/launch.json). Ejecutar con el Python del venv.
"""

import logging
import os

# Sin esto, los logs INFO de la app (p. ej. "MQTT conectado", "Robot juega
# e2e4") no llegan al journal: uvicorn solo configura sus propios loggers.
logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

os.environ.setdefault("CHESS_DRIVER", "vision")
# UR10e real por RTDE (docs/ur10e.md). Quitar/ajustar si el robot no está.
os.environ.setdefault("CHESS_ROBOT_HOST", "192.168.0.25")
# Sin el UR10e conectado, las jugadas del robot las ejecuta un humano a
# mano: darle tiempo de sobra antes de pasar a resync (que igual se
# auto-recupera al coincidir el tablero).
os.environ.setdefault("CHESS_ROBOT_VERIFY_TIMEOUT", "45")

import uvicorn

from app.api.server import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
