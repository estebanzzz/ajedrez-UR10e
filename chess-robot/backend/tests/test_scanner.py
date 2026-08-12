import threading

import chess

from app.board_sensor import BoardScanner, MockDriver
from app.board_sensor.bitmap import FULL_START_BITMAP


def make_scanner(driver: MockDriver) -> BoardScanner:
    # Rápido para que los tests no demoren: 200 Hz, 2 lecturas estables.
    return BoardScanner(driver, scan_hz=200.0, stable_reads=2)


def test_scanner_publishes_initial_stable_bitmap():
    driver = MockDriver(initial=FULL_START_BITMAP)
    scanner = make_scanner(driver)
    scanner.start()
    try:
        assert scanner.wait_for_bitmap(FULL_START_BITMAP, timeout=1.0)
    finally:
        scanner.stop()


def test_scanner_notifies_subscribers_on_change():
    driver = MockDriver(initial=FULL_START_BITMAP)
    scanner = make_scanner(driver)
    received: list[int] = []
    event = threading.Event()

    def on_bitmap(bitmap: int) -> None:
        received.append(bitmap)
        if len(received) >= 2:
            event.set()

    scanner.subscribe(on_bitmap)
    scanner.start()
    try:
        # Esperar la estabilización inicial antes de simular el cambio.
        assert scanner.wait_for_bitmap(FULL_START_BITMAP, timeout=1.0)
        expected = FULL_START_BITMAP & ~(1 << chess.E2)
        driver.set_square(chess.E2, occupied=False)
        assert event.wait(timeout=1.0)
        assert received[0] == FULL_START_BITMAP
        assert received[-1] == expected
    finally:
        scanner.stop()


def test_scanner_survives_driver_errors():
    class FlakyDriver(MockDriver):
        def __init__(self) -> None:
            super().__init__(initial=FULL_START_BITMAP)
            self.calls = 0

        def read(self) -> int:
            self.calls += 1
            if self.calls % 2 == 0:
                raise IOError("fallo I2C simulado")
            return super().read()

    driver = FlakyDriver()
    scanner = make_scanner(driver)
    scanner.start()
    try:
        assert scanner.wait_for_bitmap(FULL_START_BITMAP, timeout=1.0)
        assert scanner.read_errors > 0
    finally:
        scanner.stop()
