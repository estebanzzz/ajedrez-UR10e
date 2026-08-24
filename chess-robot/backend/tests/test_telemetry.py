"""Puente MQTT hacia Neuronal HUB: proyección numérica y publicación por cambio."""

from app.telemetry import MqttBridge, numeric_state


def status(**overrides) -> dict:
    base = {
        "phase": "human_turn",
        "turn": "white",
        "evaluation": {"cp": 35, "mate": None, "display": "+0.35"},
        "san_history": ["e4", "e5", "Nf3"],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------ numeric_state


def test_numeric_state_maps_evaluation_and_move_number():
    values = numeric_state(status())
    assert values == {
        "ajedrez_evaluacion": 0.35,
        "ajedrez_jugada_numero": 2,  # 3 plies -> jugada 2
        "ajedrez_turno": 0,
        "ajedrez_fase": 1,
        "ajedrez_partida_activa": 1,
        "ajedrez_tiempo_restante": 0,  # sin reloj
    }
    with_clock = numeric_state(
        status(clock={"limit_s": 300, "remaining_s": 187, "running": True})
    )
    assert with_clock["ajedrez_tiempo_restante"] == 187


def test_numeric_state_mate_and_black_turn():
    values = numeric_state(
        status(evaluation={"cp": None, "mate": -3, "display": "M-3"}, turn="black")
    )
    assert values["ajedrez_evaluacion"] == -99.0
    assert values["ajedrez_turno"] == 1


def test_numeric_state_idle_and_missing_fields():
    values = numeric_state({"phase": "idle"})
    assert values["ajedrez_jugada_numero"] == 0
    assert values["ajedrez_partida_activa"] == 0
    assert values["ajedrez_evaluacion"] == 0.0
    assert numeric_state(status(phase="game_over"))["ajedrez_partida_activa"] == 0
    assert numeric_state(status(phase="resync"))["ajedrez_fase"] == 4


# ----------------------------------------------------------------- MqttBridge


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def test_bridge_publishes_only_changes_with_retain():
    current = {"value": status()}
    client = FakeClient()
    bridge = MqttBridge(lambda: current["value"], host="hub.local", client=client)

    bridge.tick()
    assert len(client.published) == 6  # primera pasada: todos los topics
    assert ("neuronal/sensors/ajedrez_evaluacion", "0.35", True) in client.published
    assert ("neuronal/sensors/ajedrez_jugada_numero", "2", True) in client.published

    client.published.clear()
    bridge.tick()
    assert client.published == []  # sin cambios, sin tráfico

    current["value"] = status(turn="black", san_history=["e4", "e5", "Nf3", "Nc6"])
    bridge.tick()
    topics = {t for t, _, _ in client.published}
    assert topics == {
        "neuronal/sensors/ajedrez_turno",
        "neuronal/sensors/ajedrez_jugada_numero",
    }

    # Al reconectar se limpia el caché y se republica todo.
    bridge._on_connect()
    client.published.clear()
    bridge.tick()
    assert len(client.published) == 6
