from datetime import datetime

from app.scores import ScoreStore, compute_score


# ------------------------------------------------------------------- fórmula


def test_score_loss_still_earns_points():
    # Perder tras resistir 30 jugadas capturando 10 puntos de material
    # en intermedio: (0 + 30*4 + 10*15) * 1.5 = 405
    assert compute_score("loss", 30, 10, "intermedio") == 405


def test_score_win_beats_long_loss():
    quick_win = compute_score("win", 10, 5, "principiante")
    heroic_loss = compute_score("loss", 60, 20, "principiante")
    assert quick_win > heroic_loss


def test_score_difficulty_multiplier():
    base = compute_score("draw", 20, 0, "principiante")
    assert compute_score("draw", 20, 0, "maximo") == base * 3


def test_score_unknown_difficulty_defaults_to_1x():
    assert compute_score("loss", 10, 0, "aleatorio") == 40


def test_score_invalid_result_raises():
    import pytest

    with pytest.raises(ValueError):
        compute_score("timeout", 10, 0, "intermedio")


# --------------------------------------------------------------------- store


def make_store(tmp_path) -> ScoreStore:
    return ScoreStore(tmp_path / "scores.db")


def test_record_and_rank_today(tmp_path):
    store = make_store(tmp_path)
    store.record("Ana García", 800, "loss", "avanzado", 40, 12)
    store.record("Luis Pérez", 1500, "win", "principiante", 25, 8)
    store.record("Mia Ruiz", 300, "loss", "intermedio", 15, 2)

    today = store.top_today(limit=10)
    assert [r["name"] for r in today] == ["Luis Pérez", "Ana García", "Mia Ruiz"]
    assert today[0]["score"] == 1500


def test_daily_ranking_excludes_other_days(tmp_path):
    store = make_store(tmp_path)
    store.record("Ayer", 9999, "win", "maximo", 30, 20, when=datetime(2026, 8, 11, 18, 0))
    store.record("Hoy", 100, "loss", "principiante", 10, 0)

    today = store.top_today()
    assert [r["name"] for r in today] == ["Hoy"]
    alltime = store.top_alltime()
    assert alltime[0]["name"] == "Ayer"  # el histórico sí lo incluye


def test_ties_resolved_by_insertion_order(tmp_path):
    store = make_store(tmp_path)
    store.record("Primero", 500, "loss", "intermedio", 20, 5)
    store.record("Segundo", 500, "loss", "intermedio", 20, 5)
    assert [r["name"] for r in store.top_today()] == ["Primero", "Segundo"]


def test_persistence_across_reopen(tmp_path):
    store = make_store(tmp_path)
    store.record("Persistente", 700, "draw", "avanzado", 35, 6)
    store.close()
    reopened = make_store(tmp_path)
    assert reopened.top_alltime()[0]["name"] == "Persistente"
