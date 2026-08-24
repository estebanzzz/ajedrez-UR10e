from datetime import datetime, timedelta

from app.scores import ScoreStore, compute_score


def log_args(**overrides) -> dict:
    """Campos completos de una partida para game_log, con valores por defecto."""
    now = datetime.now()
    base = dict(
        started_ts=(now - timedelta(minutes=5)).isoformat(timespec="seconds"),
        ended_ts=now.isoformat(timespec="seconds"),
        day=now.strftime("%Y-%m-%d"),
        duration_s=300,
        mode="human",
        player_name="Ana",
        human_color="white",
        difficulty="intermedio",
        result="loss",
        chess_result="0-1",
        termination="CHECKMATE",
        moves=24,
        san="e4 e5 Nf3",
        final_fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        score=250,
        material=4,
    )
    base.update(overrides)
    return base


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


# ------------------------------------------------------- game_log / stats


def test_game_log_summary_and_listing(tmp_path):
    store = ScoreStore(tmp_path / "scores.db")
    store.log_game(**log_args())
    store.log_game(**log_args(player_name="Beto", result="win", chess_result="1-0", score=1400))
    store.log_game(**log_args(mode="self_play", player_name="", human_color=None, result=None, score=None))
    store.log_game(**log_args(result="abandoned", termination="RESIGNATION"))

    summary = store.stats_summary(days=1)
    assert summary["totals"]["games"] == 4
    assert summary["totals"]["human_games"] == 3
    assert summary["totals"]["demos"] == 1
    assert summary["totals"]["players"] == 2  # Ana y Beto
    assert summary["totals"]["top_score"] == 1400
    assert summary["by_result"] == {"loss": 1, "win": 1, "demo": 1, "abandoned": 1}
    assert summary["by_difficulty"] == {"intermedio": 3}
    assert len(summary["per_day"]) == 1 and summary["per_day"][0]["games"] == 4

    listing = store.list_games(limit=2)
    assert listing["total"] == 4
    assert len(listing["games"]) == 2
    assert "san" not in listing["games"][0]  # listado liviano

    only_demos = store.list_games(mode="self_play")
    assert only_demos["total"] == 1

    detail = store.game_detail(listing["games"][0]["id"])
    assert detail["san"] == "e4 e5 Nf3"
    assert store.game_detail(99999) is None
    store.close()


def test_game_log_summary_empty_store(tmp_path):
    store = ScoreStore(tmp_path / "scores.db")
    summary = store.stats_summary(days=7)
    assert summary["totals"]["games"] == 0
    assert summary["by_result"] == {}
    assert summary["per_day"] == []
    store.close()
