from app.board_sensor.debounce import Debouncer

A = 0b0001
B = 0b0010


def test_first_stabilization_after_n_reads():
    debouncer = Debouncer(stable_reads=3)
    assert debouncer.feed(A) is None
    assert debouncer.feed(A) is None
    assert debouncer.feed(A) == A
    assert debouncer.stable == A


def test_stable_reads_do_not_reemit():
    debouncer = Debouncer(stable_reads=2)
    debouncer.feed(A)
    debouncer.feed(A)
    assert debouncer.feed(A) is None
    assert debouncer.feed(A) is None


def test_glitch_is_rejected():
    debouncer = Debouncer(stable_reads=3)
    for _ in range(3):
        debouncer.feed(A)
    # Rebote de un ciclo: no debe emitirse.
    assert debouncer.feed(B) is None
    assert debouncer.feed(A) is None
    assert debouncer.stable == A


def test_change_confirmed_after_stability():
    debouncer = Debouncer(stable_reads=2)
    debouncer.feed(A)
    debouncer.feed(A)
    assert debouncer.feed(B) is None
    assert debouncer.feed(B) == B
    assert debouncer.stable == B


def test_alternating_noise_never_stabilizes():
    debouncer = Debouncer(stable_reads=3)
    debouncer.feed(A)
    for _ in range(10):
        assert debouncer.feed(B) is None
        assert debouncer.feed(A) is None
