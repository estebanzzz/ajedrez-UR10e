"""Voz del robot: Commentator (reglas), Heckler (disparadores por tiempo) e
integración con el orquestador (qué evento sale en cada jugada)."""

import random

import chess
import pytest

from app.personality import Commentator, Event, Heckler
from app.personality.commentator import COOLDOWN_S, Utterance
from app.personality.heckler import ATTRACT_EVERY_S
from test_orchestrator import Rig

BANK = {
    "lines": {
        "illegal_move": ["i1", "i2", "i3"],
        "filler": ["[laughs] f1", "f2"],
        "robot_check": ["c1"],
        "robot_wins": ["w1"],
        "game_start": ["g1"],
        "attract": ["a1"],
        "human_slow_20": ["s20"],
        "human_slow_45": ["s45"],
        "human_slow_90": ["s90"],
        "piece_lifted": ["p1"],
        "piece_returned": ["r1"],
        "self_play": ["sp1"],
        "boot": ["b1"],
    },
    "sfx": {},
}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(level=2, clock=None):
    return Commentator(BANK, manifest={}, level=level, rng=random.Random(1), clock=clock or Clock())


# ------------------------------------------------------------ Commentator


def test_level_zero_is_mute_and_force_overrides():
    c = make(level=0)
    assert c.say(Event.ROBOT_WINS) is None
    assert c.say(Event.ROBOT_WINS, force=True).text == "w1"


def test_level_one_skips_distractions_but_comments_the_game():
    c = make(level=1)
    assert c.say(Event.FILLER) is None
    assert c.say(Event.ILLEGAL_MOVE) is not None


def test_lines_do_not_repeat_within_a_game():
    c = make()
    said = [c.say(Event.ILLEGAL_MOVE).text for _ in range(3)]
    assert sorted(said) == ["i1", "i2", "i3"]
    # Agotadas las tres, vuelve a empezar.
    assert c.say(Event.ILLEGAL_MOVE).text in said
    c.new_game()
    assert c.say(Event.ILLEGAL_MOVE).text in said


def test_tags_are_stripped_for_display():
    c = make()
    texts = {c.say(Event.FILLER, force=True).text for _ in range(2)}
    assert texts == {"f1", "f2"}


def test_low_priority_respects_cooldown_high_priority_does_not():
    clock = Clock()
    c = make(clock=clock)
    assert c.say(Event.ILLEGAL_MOVE) is not None
    clock.now += 1
    assert c.say(Event.FILLER) is None  # prioridad 0 dentro del enfriamiento
    assert c.say(Event.ROBOT_CHECK) is not None  # prioridad 2 sale igual
    clock.now += COOLDOWN_S
    assert c.say(Event.FILLER) is not None


def test_self_play_filters_human_only_events():
    c = make()
    assert c.say(Event.GAME_START, self_play=True) is None
    assert c.say(Event.SELF_PLAY, self_play=True) is not None


def test_manifest_maps_text_to_audio_file(tmp_path, monkeypatch):
    import app.personality.commentator as mod

    monkeypatch.setattr(mod, "VOICE_DIR", tmp_path)
    (tmp_path / "w.mp3").write_bytes(b"ID3")
    (tmp_path / "fan.mp3").write_bytes(b"ID3")
    manifest = {
        "lines": {"robot_wins": [{"text": "w1", "tags": [], "file": "w.mp3"}]},
        "sfx": {"fanfare": "fan.mp3", "applause": "missing.mp3"},
    }
    c = Commentator(BANK, manifest=manifest, rng=random.Random(0))
    assert c.has_audio
    said = c.say(Event.ROBOT_WINS)
    assert said.audio == "w.mp3"
    assert said.sfx == "fan.mp3"  # el único efecto existente de los candidatos
    d = said.as_dict()
    assert d["audio_url"] == "/voice/w.mp3" and d["sfx_url"] == "/voice/fan.mp3"
    assert c.status()["last"]["seq"] == said.seq


def test_listeners_receive_each_utterance():
    c = make()
    heard: list[Utterance] = []
    c.subscribe(heard.append)
    c.say(Event.ROBOT_CHECK)
    assert [u.text for u in heard] == ["c1"]


def test_personalities_inherit_switch_and_audio(tmp_path, monkeypatch):
    import app.personality.commentator as mod

    monkeypatch.setattr(mod, "VOICE_DIR", tmp_path)
    (tmp_path / "a.mp3").write_bytes(b"ID3")
    (tmp_path / "b.mp3").write_bytes(b"ID3")
    bank = {"lines": {"robot_wins": ["gana base"], "resync": ["resync base"]}, "sfx": {}}
    config = {
        "default": "clasico",
        "personalities": {
            "clasico": {"label": "Clásico", "lines_file": "lines.es.json"},
            "agus": {"label": "Agustín", "lines": {"robot_wins": ["gana agus"]}},
        },
    }
    manifest = {
        "sfx": {},
        "personalities": {
            "clasico": {"lines": {"robot_wins": [{"text": "gana base", "tags": [], "file": "a.mp3"}]}},
            "agus": {"lines": {"robot_wins": [{"text": "gana agus", "tags": [], "file": "b.mp3"}]}},
        },
    }
    c = Commentator(bank, manifest=manifest, config=config, rng=random.Random(0))
    assert [p["id"] for p in c.status()["personalities"]] == ["clasico", "agus"]
    said = c.say(Event.ROBOT_WINS)
    assert (said.text, said.audio) == ("gana base", "a.mp3")

    c.set_personality("agus")
    assert c.status()["personality"] == "agus"
    said = c.say(Event.ROBOT_WINS)
    assert (said.text, said.audio) == ("gana agus", "b.mp3")
    # Lo que agus no define lo hereda del banco base.
    assert c.say(Event.RESYNC).text == "resync base"
    with pytest.raises(ValueError):
        c.set_personality("nadie")


def test_real_config_loads_both_personalities():
    c = Commentator(level=2, rng=random.Random(0))
    assert [p["id"] for p in c.personalities()] == ["clasico", "agustin"]
    c.set_personality("agustin")
    # Categoría heredada del banco base y categoría propia.
    assert c.say(Event.RESYNC, force=True) is not None
    assert c.say(Event.HUMAN_GOOD_MOVE, force=True) is not None


def test_set_level_validates():
    c = make()
    with pytest.raises(ValueError):
        c.set_level(5)
    c.set_level(0)
    assert c.status()["level"] == 0


# ---------------------------------------------------------------- Heckler


class FakeGame:
    def __init__(self) -> None:
        self.phase = "idle"
        self.detector = "idle"
        self.mode = "human"

    def status(self) -> dict:
        return {"phase": self.phase, "detector_phase": self.detector, "mode": self.mode}


def heckler_rig(boot_delay=None):
    clock = Clock()
    c = make(clock=clock)
    events: list[str] = []
    c.subscribe(lambda u: events.append(u.event))
    game = FakeGame()
    h = Heckler(c, game.status, interval=1.0, clock=clock, rng=random.Random(0), boot_delay=boot_delay)
    return clock, c, events, game, h


def test_boot_line_after_delay():
    clock, _, events, _, h = heckler_rig(boot_delay=3.0)
    h.tick()
    assert events == []
    clock.now += 3
    h.tick()
    assert events == ["boot"]


def test_slow_steps_fire_once_per_human_turn():
    clock, _, events, game, h = heckler_rig()
    game.phase = "human_turn"
    h.tick()
    events.clear()
    for _ in range(100):
        clock.now += 1
        h.tick()
    slow = [e for e in events if e.startswith("human_slow")]
    assert slow == ["human_slow_20", "human_slow_45", "human_slow_90"]
    # Nuevo turno: se rearma.
    game.phase = "robot_turn"
    h.tick()
    game.phase = "human_turn"
    h.tick()
    events.clear()
    clock.now += 21
    h.tick()
    assert "human_slow_20" in events


def test_piece_lifted_and_returned():
    clock, _, events, game, h = heckler_rig()
    game.phase = "human_turn"
    h.tick()
    events.clear()
    game.detector = "in_progress"
    h.tick()
    assert events == ["piece_lifted"]
    clock.now += COOLDOWN_S + 1
    game.detector = "idle"
    h.tick()
    assert events == ["piece_lifted", "piece_returned"]


def test_attract_mode_when_idle():
    clock, _, events, _, h = heckler_rig()
    h.tick()
    clock.now += ATTRACT_EVERY_S - 1
    h.tick()
    assert events == []
    clock.now += 1
    h.tick()
    assert events == ["attract"]


# ---------------------------------------------------------- Orquestador


def voiced_rig(engine_moves):
    clock = Clock()
    c = make(clock=clock)
    events: list[str] = []
    c.subscribe(lambda u: events.append(u.event))
    bank_full = Commentator(level=2, rng=random.Random(0), clock=clock)  # banco real
    c._banks = bank_full._banks  # todas las categorías y personalidades, sin audio
    return Rig(engine_moves, commentator=c), events, clock


def test_orchestrator_announces_start_illegal_capture_and_loss():
    # El robot (negras) come en d5; el humano termina dando mate del pastor.
    rig, events, clock = voiced_rig(["e7e5", "b8c6", "g8f6"])
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE)
        assert events[0] == "game_start"

        bad = (chess.Board().occupied & ~(1 << chess.E2)) | (1 << chess.E5)
        rig.driver.set_bitmap(bad)
        assert rig.scanner.wait_for_bitmap(bad, timeout=1.0)
        rig.orchestrator.confirm()
        assert "illegal_move" in events
        rig.driver.set_bitmap(chess.Board().occupied)
        assert rig.scanner.wait_for_bitmap(chess.Board().occupied, timeout=1.0)

        for uci in ["e2e4", "f1c4", "d1h5"]:
            clock.now += COOLDOWN_S + 1
            assert rig.human_plays(uci)["phase"] == "human_turn"
        status = rig.human_plays("h5f7")
        assert status["phase"] == "game_over"
        assert events[-1] == "robot_loses"
        assert status["speech"]["last"]["event"] == "robot_loses"
    finally:
        rig.stop()


def test_orchestrator_announces_robot_capture_and_check():
    # 1.e4 e5 2.Nf3 d6 3.Nxe5 (captura) ... luego Bb5+ (jaque) con dxe5 de por medio
    rig, events, clock = voiced_rig(["e7e5", "d8h4"])  # 1...e5, 2...Qh4 tras 2.f3?? no: 2.g3
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE)
        clock.now += COOLDOWN_S + 1
        rig.human_plays("e2e4")  # 1.e4 e5
        clock.now += COOLDOWN_S + 1
        rig.human_plays("f2f3")  # 2.f3 Qh4+ (jaque)
        assert events[-1] == "robot_check"
    finally:
        rig.stop()


def test_orchestrator_stop_counts_as_abandon():
    rig, events, _ = voiced_rig([])
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE)
        rig.orchestrator.stop_game()
        assert events[-1] == "game_stop"
    finally:
        rig.stop()


def test_orchestrator_resign_speaks_game_stop():
    rig, events, _ = voiced_rig([])
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE)
        rig.orchestrator.resign()
        assert events[-1] == "game_stop"
    finally:
        rig.stop()


def test_orchestrator_timeout_speaks_timeout():
    import time as real_time

    rig, events, _ = voiced_rig([])
    try:
        rig.orchestrator.new_game(human_color=chess.WHITE, time_limit_s=0.4)
        deadline = real_time.time() + 3.0
        while real_time.time() < deadline and "timeout" not in events:
            real_time.sleep(0.05)
        assert events[-1] == "timeout"
    finally:
        rig.stop()
