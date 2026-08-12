"""Tests del driver S7 (PLC) con cliente snap7 falso — sin hardware."""

import chess

from app.board_sensor.s7 import (
    PHASE_STATUS_CODES,
    PanelLink,
    S7Driver,
    STATUS_ERROR,
    STATUS_HUMAN_TURN,
    STATUS_OFFSET_OWN_DB,
    STATUS_OFFSET_SHARED_DB,
    STATUS_ROBOT_MOVING,
)


class FakeS7Client:
    """Simula los DBs del PLC según el layout documentado."""

    def __init__(self) -> None:
        self.dbs: dict[int, bytearray] = {1: bytearray(14), 2: bytearray(2)}
        self.writes: list[tuple[int, int, bytes]] = []  # (db, offset, datos)
        self.disconnected = False

    def db_read(self, db_number: int, start: int, size: int) -> bytearray:
        return bytearray(self.dbs[db_number][start : start + size])

    def db_write(self, db_number: int, start: int, data: bytearray) -> None:
        self.dbs[db_number][start : start + len(data)] = data
        self.writes.append((db_number, start, bytes(data)))

    def disconnect(self) -> None:
        self.disconnected = True

    # helpers de test
    def set_board(self, squares: list[int]) -> None:
        bitmap = 0
        for square in squares:
            bitmap |= 1 << square
        self.dbs[1][0:8] = bitmap.to_bytes(8, "little")

    def set_panel(self, button: bool = False, estop_ok: bool = True) -> None:
        self.dbs[1][8] = (0x01 if button else 0) | (0x02 if estop_ok else 0)

    def set_heartbeat(self, value: int) -> None:
        self.dbs[1][10:12] = value.to_bytes(2, "big")


def make_driver() -> tuple[S7Driver, FakeS7Client]:
    client = FakeS7Client()
    client.set_panel(estop_ok=True)
    return S7Driver(client), client


# ------------------------------------------------------------------ lectura


def test_empty_board_reads_zero():
    driver, _ = make_driver()
    assert driver.read() == 0


def test_square_mapping_matches_convention():
    driver, client = make_driver()
    client.set_board([chess.A1, chess.E4, chess.H8])
    assert driver.read() == (1 << chess.A1) | (1 << chess.E4) | (1 << chess.H8)


def test_starting_position_roundtrip():
    driver, client = make_driver()
    occupied = chess.Board().occupied
    client.dbs[1][0:8] = occupied.to_bytes(8, "little")
    assert driver.read() == occupied


def test_panel_state_travels_with_read():
    driver, client = make_driver()
    client.set_panel(button=True, estop_ok=False)
    client.set_heartbeat(41)
    driver.read()
    assert driver.button_pressed is True
    assert driver.estop_ok is False
    assert driver.heartbeat == 41


def test_plc_alive_tracks_heartbeat_changes():
    driver, client = make_driver()
    client.set_heartbeat(1)
    driver.read()
    assert driver.plc_alive(max_age_s=1.0)
    # Sin cambios de heartbeat el PLC se considera muerto pasada la ventana.
    driver.read()
    assert not driver.plc_alive(max_age_s=0.0)
    client.set_heartbeat(2)
    driver.read()
    assert driver.plc_alive(max_age_s=1.0)


def test_write_status_goes_to_output_db():
    # Esquema por defecto: DB1 entradas (PLC→Pi), DB2 salidas (Pi→PLC).
    driver, client = make_driver()
    driver.write_status(STATUS_ROBOT_MOVING)
    assert client.writes == [(2, STATUS_OFFSET_OWN_DB, bytes([STATUS_ROBOT_MOVING]))]
    driver.close()
    assert client.disconnected


def test_single_shared_db_uses_offset_12():
    client = FakeS7Client()
    driver = S7Driver(client, db_in=1, db_out=1)
    driver.write_status(STATUS_ROBOT_MOVING)
    assert client.writes == [(1, STATUS_OFFSET_SHARED_DB, bytes([STATUS_ROBOT_MOVING]))]


# ---------------------------------------------------------------- PanelLink


def test_button_rising_edge_triggers_confirm_once():
    driver, client = make_driver()
    presses: list[int] = []
    link = PanelLink(driver, get_phase=lambda: "human_turn", on_button=lambda: presses.append(1))

    link.poll_once()  # botón suelto
    client.set_panel(button=True)
    driver.read()
    link.poll_once()  # flanco ascendente → confirm
    link.poll_once()  # sigue apretado → no repite
    client.set_panel(button=False)
    driver.read()
    link.poll_once()  # suelto
    client.set_panel(button=True)
    driver.read()
    link.poll_once()  # nuevo flanco → segundo confirm
    assert len(presses) == 2


def test_status_written_only_on_phase_change():
    driver, client = make_driver()
    phase = {"value": "human_turn"}
    link = PanelLink(driver, get_phase=lambda: phase["value"], on_button=lambda: None)

    link.poll_once()
    link.poll_once()
    assert client.writes == [(2, STATUS_OFFSET_OWN_DB, bytes([STATUS_HUMAN_TURN]))]

    phase["value"] = "resync"
    link.poll_once()
    assert client.writes[-1] == (2, STATUS_OFFSET_OWN_DB, bytes([STATUS_ERROR]))


def test_all_phases_have_status_code():
    from app.game_state.orchestrator import MatchPhase

    for match_phase in MatchPhase:
        assert match_phase.value in PHASE_STATUS_CODES
