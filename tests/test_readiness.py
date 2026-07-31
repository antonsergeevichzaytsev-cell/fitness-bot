"""Тесты для readiness.py — многосигнальная оценка готовности.

Архитектурный принцип, который тестируем явно: compute_readiness_score
ДЕТЕРМИНИРОВАННАЯ, не зависит от сети/LLM. explain_readiness может
использовать DeepSeek, но при сбое падает на _fallback_explanation,
не ломает функцию целиком.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("DEEPSEEK_API_KEY", "")

sys.path.insert(0, "..")
import readiness as r
import workouts as w


# --- collect_signals ---------------------------------------------------

def test_collect_signals_no_data_all_none():
    data = w.load_workouts()
    data["sets"] = []
    data["wellness_log"] = {}
    data["skipped_days"] = {}
    signals = r.collect_signals(data)
    assert signals["sleep_hours"] is None
    assert signals["stress_level"] is None
    assert signals["recent_high_rpe_count"] == 0
    assert signals["recent_skip_within_days"] is None
    assert signals["tonnage_trend_pct"] is None


def test_collect_signals_picks_latest_wellness():
    data = w.load_workouts()
    data["wellness_log"] = {}
    w.save_wellness_for_date(data, "2026-07-20", sleep_hours=7.0, stress_level=3)
    w.save_wellness_for_date(data, "2026-07-27", sleep_hours=5.0, stress_level=8)
    signals = r.collect_signals(data)
    assert signals["sleep_hours"] == 5.0  # самая свежая запись
    assert signals["stress_level"] == 8
    assert signals["wellness_date"] == "2026-07-27"


def test_collect_signals_counts_high_rpe():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-28", 40.0, 8, 1, rpe=9)
    w.add_set(data, "жим", "2026-07-28", 40.0, 8, 2, rpe=6)
    signals = r.collect_signals(data)
    assert signals["recent_high_rpe_count"] == 1
    assert signals["recent_sets_checked"] == 2


def test_collect_signals_finds_recent_skip():
    data = w.load_workouts()
    data["skipped_days"] = {}
    skip_date = (datetime.now(timezone.utc) - timedelta(days=3)).date().isoformat()
    w.mark_day_skipped(data, skip_date, reason="болею")
    signals = r.collect_signals(data)
    assert signals["recent_skip_within_days"] == 3


def test_collect_signals_no_skip_outside_lookback_window():
    data = w.load_workouts()
    data["skipped_days"] = {}
    old_skip_date = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    w.mark_day_skipped(data, old_skip_date, reason="болею")
    signals = r.collect_signals(data)
    assert signals["recent_skip_within_days"] is None  # за пределами 14-дневного окна


def test_collect_signals_tonnage_trend_with_exercise():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "присед", "2026-07-13", 60.0, 10, 1)
    w.add_set(data, "присед", "2026-07-20", 45.0, 10, 1)
    signals = r.collect_signals(data, exercise_for_trend="присед")
    assert signals["tonnage_trend_pct"] == -25


def test_collect_signals_no_trend_without_exercise_param():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "присед", "2026-07-13", 60.0, 10, 1)
    w.add_set(data, "присед", "2026-07-20", 45.0, 10, 1)
    signals = r.collect_signals(data)  # без exercise_for_trend
    assert signals["tonnage_trend_pct"] is None


# --- compute_readiness_score ---------------------------------------

def test_score_perfect_when_no_negative_signals():
    signals = {"sleep_hours": 8.0, "stress_level": 3, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 0, "recent_sets_checked": 5,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 100
    assert result["factors"] == []


def test_score_no_data_not_penalized():
    # Честность: отсутствие данных о самочувствии НЕ штрафуется —
    # нечестно наказывать за то, чего не вводили
    signals = {"sleep_hours": None, "stress_level": None, "wellness_date": None,
               "recent_high_rpe_count": 0, "recent_sets_checked": 0,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 100
    assert result["factors"] == []


def test_score_low_sleep_penalty():
    signals = {"sleep_hours": 4.5, "stress_level": None, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 0, "recent_sets_checked": 0,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 80  # 100 - 20
    assert len(result["factors"]) == 1


def test_score_high_stress_penalty():
    signals = {"sleep_hours": None, "stress_level": 9, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 0, "recent_sets_checked": 0,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 80  # 100 - 20


def test_score_all_five_signals_cumulative():
    signals = {"sleep_hours": 5.0, "stress_level": 8, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 1, "recent_sets_checked": 4,
               "recent_skip_within_days": 3, "tonnage_trend_pct": -25}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 20  # 100-20-20-15-10-15
    assert len(result["factors"]) == 5


def test_score_never_goes_below_zero():
    signals = {"sleep_hours": 3.0, "stress_level": 10, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 5, "recent_sets_checked": 5,
               "recent_skip_within_days": 1, "tonnage_trend_pct": -50}
    result = r.compute_readiness_score(signals)
    assert result["score"] >= 0


def test_score_boundary_sleep_exactly_6h_not_penalized():
    signals = {"sleep_hours": 6.0, "stress_level": None, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 0, "recent_sets_checked": 0,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 100  # ровно 6, не < 6


def test_score_boundary_stress_exactly_7_penalized():
    signals = {"sleep_hours": None, "stress_level": 7, "wellness_date": "2026-07-28",
               "recent_high_rpe_count": 0, "recent_sets_checked": 0,
               "recent_skip_within_days": None, "tonnage_trend_pct": None}
    result = r.compute_readiness_score(signals)
    assert result["score"] == 80  # >= 7, штрафуется


# --- explain_readiness / _fallback_explanation ------------------------

def test_fallback_explanation_no_factors():
    result = {"score": 100, "factors": []}
    text = r._fallback_explanation(result)
    assert "нет" in text.lower()


def test_fallback_explanation_low_score_recommends_rest():
    result = {"score": 20, "factors": ["сон 4ч"]}
    text = r._fallback_explanation(result)
    assert "отдохнуть" in text.lower() or "лёгк" in text.lower()


def test_fallback_explanation_mid_score_recommends_lighter():
    result = {"score": 55, "factors": ["стресс 8/10"]}
    text = r._fallback_explanation(result)
    assert "снизить" in text.lower()


def test_explain_readiness_no_api_key_uses_fallback():
    with mock.patch("readiness.DEEPSEEK_KEY", ""):
        signals = {"sleep_hours": 8.0, "stress_level": 3, "wellness_date": "2026-07-28",
                   "recent_high_rpe_count": 0, "recent_sets_checked": 0,
                   "recent_skip_within_days": None, "tonnage_trend_pct": None}
        result = {"score": 100, "factors": []}
        text = r.explain_readiness(signals, result)
    assert "нет" in text.lower()  # fallback text, not LLM


def test_explain_readiness_network_failure_falls_back():
    with mock.patch("readiness.DEEPSEEK_KEY", "test"), \
         mock.patch("readiness.net.urlopen_retry", side_effect=Exception("network down")):
        signals = {"sleep_hours": 5.0, "stress_level": 8, "wellness_date": "2026-07-28",
                   "recent_high_rpe_count": 0, "recent_sets_checked": 0,
                   "recent_skip_within_days": None, "tonnage_trend_pct": None}
        result = {"score": 60, "factors": ["сон 5.0ч (ниже 6ч)", "стресс 8/10 (выше 7)"]}
        text = r.explain_readiness(signals, result)
    assert "Факторы" in text  # fallback format, not LLM


# --- format_readiness_report -----------------------------------------

def test_format_readiness_report_includes_disclaimer():
    data = w.load_workouts()
    data["sets"] = []
    data["wellness_log"] = {}
    data["skipped_days"] = {}
    w.save_wellness_for_date(data, "2026-07-28", sleep_hours=8.0, stress_level=3)
    report = r.format_readiness_report(data)
    assert "носимого устройства" in report or "HRV" in report


def test_format_readiness_report_shows_score():
    data = w.load_workouts()
    data["sets"] = []
    data["wellness_log"] = {}
    data["skipped_days"] = {}
    w.save_wellness_for_date(data, "2026-07-28", sleep_hours=8.0, stress_level=3)
    report = r.format_readiness_report(data)
    assert "100/100" in report


def test_format_readiness_report_low_score_uses_red_emoji():
    data = w.load_workouts()
    data["sets"] = []
    data["wellness_log"] = {}
    data["skipped_days"] = {}
    w.save_wellness_for_date(data, "2026-07-28", sleep_hours=3.0, stress_level=10)
    # Только сон+стресс дают 60 (жёлтая зона) — для красной (<40) нужен
    # дополнительный штраф, например недавний пропуск
    skip_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    w.mark_day_skipped(data, skip_date, reason="болею")
    w.add_set(data, "жим", "2026-07-27", 40.0, 8, 1, rpe=9)
    report = r.format_readiness_report(data)
    assert "\U0001f534" in report  # красный кружок при низкой оценке
