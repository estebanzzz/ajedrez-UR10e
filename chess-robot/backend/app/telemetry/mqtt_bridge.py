"""Puente MQTT hacia Neuronal HUB (supervisión de la feria).

Neuronal HUB consume "sensores" por MQTT: este módulo publica los valores
numéricos del estado de la partida en ``neuronal/sensors/<nombre>`` cada vez
que cambian (mismo criterio que el push de ``/ws/game``: un hilo sondea
``status()`` y publica solo lo que cambió).

Topics (payload: número plano como string, con ``retain`` para que el HUB
reciba el último valor al suscribirse):

- ``ajedrez_evaluacion``     — evaluación en peones, positivo = ventaja
  blancas; mate forzado se publica como ±99.
- ``ajedrez_jugada_numero``  — número de jugada (0 sin partida).
- ``ajedrez_turno``          — 0 blancas, 1 negras.
- ``ajedrez_fase``           — 0 idle · 1 turno humano · 2 error humano ·
  3 turno robot · 4 resync · 5 fin de partida.
- ``ajedrez_partida_activa`` — 1 si hay partida en curso.
- ``ajedrez_tiempo_restante`` — segundos que le quedan al humano en el reloj
  (0 si no hay reloj o no hay partida).

Se activa con ``CHESS_MQTT_HOST`` (más usuario/contraseña que pasa el equipo
del HUB); sin esa variable el backend ni siquiera importa paho-mqtt. La
conexión es asíncrona con reconexión automática: si el broker no está cuando
arranca la Pi, el puente engancha solo cuando aparezca, y al reconectar
republica todos los valores.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

TOPIC_PREFIX = "neuronal/sensors/"
MATE_EVAL = 99.0  # los dashboards grafican tendencia: mate = valor tope legible

PHASE_CODES = {
    "idle": 0,
    "human_turn": 1,
    "human_error": 2,
    "robot_turn": 3,
    "resync": 4,
    "game_over": 5,
}


def numeric_state(status: dict) -> dict[str, float | int]:
    """Proyección numérica del status de la partida (los "sensores" del HUB)."""
    evaluation_info = status.get("evaluation") or {}
    if evaluation_info.get("mate") is not None:
        evaluation = MATE_EVAL if evaluation_info["mate"] > 0 else -MATE_EVAL
    elif evaluation_info.get("cp") is not None:
        evaluation = round(evaluation_info["cp"] / 100.0, 2)
    else:
        evaluation = 0.0
    phase = status.get("phase")
    plies = len(status.get("san_history") or [])
    active = phase not in ("idle", "game_over", None)
    return {
        "ajedrez_evaluacion": evaluation,
        "ajedrez_jugada_numero": (plies // 2) + 1 if phase not in ("idle", None) else 0,
        "ajedrez_turno": 0 if status.get("turn") == "white" else 1,
        "ajedrez_fase": PHASE_CODES.get(phase, 0),
        "ajedrez_partida_activa": 1 if active else 0,
        "ajedrez_tiempo_restante": (status.get("clock") or {}).get("remaining_s", 0),
    }


class MqttBridge:
    def __init__(
        self,
        get_status: Callable[[], dict],
        *,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        client_id: str = "chess-backend",
        topic_prefix: str = TOPIC_PREFIX,
        interval: float = 0.5,
        client=None,  # inyectable para tests (mismo contrato que paho)
    ) -> None:
        self._get_status = get_status
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client_id = client_id
        self._prefix = topic_prefix
        self._interval = interval
        self._client = client
        self._lock = threading.Lock()
        self._last: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- ciclo

    def start(self) -> None:
        if self._thread is not None:
            return
        if self._client is None:
            try:
                self._client = self._build_client()
            except Exception:
                logger.exception(
                    "MQTT deshabilitado: no se pudo crear el cliente "
                    "(¿falta `pip install paho-mqtt`?)"
                )
                return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mqtt-bridge", daemon=True)
        self._thread.start()
        logger.info("Puente MQTT hacia %s:%s activo", self._host, self._port)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                logger.debug("Error al cerrar el cliente MQTT", exc_info=True)

    def _build_client(self):
        import paho.mqtt.client as mqtt

        try:  # paho >= 2.0
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id
            )
        except AttributeError:  # paho 1.x
            client = mqtt.Client(client_id=self._client_id)
        if self._username:
            client.username_pw_set(self._username, self._password)
        client.on_connect = self._on_connect
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        # connect_async + loop_start: si el broker todavía no está (orden de
        # encendido del stand), reintenta solo sin frenar el arranque.
        client.connect_async(self._host, self._port)
        client.loop_start()
        return client

    def _on_connect(self, *_args, **_kwargs) -> None:
        # Al (re)conectar, republicar todo aunque no haya cambiado.
        with self._lock:
            self._last.clear()
        logger.info("MQTT conectado a %s:%s", self._host, self._port)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.tick()
            except Exception:
                logger.exception("Fallo publicando telemetría MQTT")

    # --------------------------------------------------------------- lógica

    def tick(self) -> None:
        """Publica los valores que cambiaron. Público y sin hilos para tests."""
        values = numeric_state(self._get_status())
        with self._lock:
            for name, value in values.items():
                payload = format(value, "g") if isinstance(value, float) else str(value)
                if self._last.get(name) == payload:
                    continue
                self._client.publish(self._prefix + name, payload, retain=True)
                self._last[name] = payload
