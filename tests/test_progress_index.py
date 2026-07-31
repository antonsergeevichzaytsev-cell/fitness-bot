"""Тесты для progress_index.py — единая метрика прогресса
(объём + сила + постоянство).
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "..")
import progress_index as pi
import workouts as w

NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)  # пятница


# --- compute_volume_component -------------------------------------

def test_volume_neutral_without_prior_period_data():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-31", 40.0, 8, 1)
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["score"] == 50
    assert result["change_pct"] is None


def test_volume_positive_growth():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 40.0, 8, 1)  # прошлая неделя, тоннаж 320
    w.add_set(data, "жим", "2026-07-31", 50.0, 8, 1)  # эта неделя, тоннаж 400 (+25%)
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["change_pct"] == 25
    assert result["score"] == 75


def test_volume_decline():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 50.0, 8, 1)  # тоннаж 400
    w.add_set(data, "жим", "2026-07-31", 40.0, 8, 1)  # тоннаж 320 (-20%)
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["change_pct"] == -20
    assert result["score"] == 30


def test_volume_score_capped_at_100():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 10.0, 8, 1)  # тоннаж 80
    w.add_set(data, "жим", "2026-07-31", 100.0, 8, 1)  # тоннаж 800, +900%
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["score"] == 100  # не больше 100


def test_volume_score_floored_at_0():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 100.0, 8, 1)  # тоннаж 800
    w.add_set(data, "жим", "2026-07-31", 5.0, 8, 1)  # тоннаж 40, огромное падение
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["score"] == 0


def test_volume_excludes_warmup_sets():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-31", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-31", 20.0, 10, 2, set_type="warmup")
    result = pi.compute_volume_component(data, days=7, now=NOW)
    assert result["change_pct"] == 0  # разминка не должна повлиять на сравнение


# --- compute_strength_component -------------------------------------

def test_strength_neutral_without_history():
    data = w.load_workouts()
    data["sets"] = []
    result = pi.compute_strength_component(data)
    assert result["score"] == 50
    assert result["avg_trend_pct"] is None


def test_strength_neutral_with_single_session():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-27", 40.0, 8, 1)  # только одна сессия
    result = pi.compute_strength_component(data)
    assert result["score"] == 50  # недостаточно истории для тренда


def test_strength_positive_trend():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-13", 40.0, 8, 1)  # 1RM ~50.67
    w.add_set(data, "жим", "2026-07-27", 45.0, 8, 1)  # 1RM = 57.0, рост
    result = pi.compute_strength_component(data)
    assert result["avg_trend_pct"] > 0
    assert result["score"] > 50


def test_strength_averages_across_multiple_exercises():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-13", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-27", 45.0, 8, 1)  # растёт
    w.add_set(data, "присед", "2026-07-13", 60.0, 8, 1)
    w.add_set(data, "присед", "2026-07-27", 55.0, 8, 1)  # падает
    result = pi.compute_strength_component(data)
    assert result["exercises_tracked"] == 2


# --- compute_consistency_component ------------------------------------

def test_consistency_perfect_when_all_trained():
    data = w.load_workouts()
    data["sets"] = []
    # неделя 25-31 июля: тренировочные дни 25, 27, 29, 31 (4 дня)
    for date in ["2026-07-25", "2026-07-27", "2026-07-29", "2026-07-31"]:
        w.add_set(data, "жим", date, 40.0, 8, 1)
    result = pi.compute_consistency_component(data, days=7, now=NOW)
    assert result["score"] == 100
    assert result["completed"] == 4
    assert result["scheduled"] == 4


def test_consistency_partial():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-27", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-31", 40.0, 8, 1)
    result = pi.compute_consistency_component(data, days=7, now=NOW)
    assert result["completed"] == 2
    assert result["scheduled"] == 4
    assert result["score"] == 50


def test_consistency_zero_when_nothing_trained():
    data = w.load_workouts()
    data["sets"] = []
    result = pi.compute_consistency_component(data, days=7, now=NOW)
    assert result["score"] == 0
    assert result["completed"] == 0


# --- compute_progress_index / format_progress_index_report -----------

def test_progress_index_averages_three_components():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-24", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-31", 50.0, 8, 1)
    result = pi.compute_progress_index(data, days=7, now=NOW)
    expected = round((result["volume"]["score"] + result["strength"]["score"] + result["consistency"]["score"]) / 3)
    assert result["index"] == expected


def test_format_report_shows_all_three_components():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим", "2026-07-13", 40.0, 8, 1)
    w.add_set(data, "жим", "2026-07-27", 45.0, 8, 1)
    report = pi.format_progress_index_report(data, days=7, now=NOW)
    assert "Объём" in report
    assert "Сила" in report
    assert "Постоянство" in report
    assert "Индекс прогресса" in report


def test_format_report_week_naming():
    data = w.load_workouts()
    data["sets"] = []
    report = pi.format_progress_index_report(data, days=7, now=NOW)
    assert "неделю" in report


def test_format_report_month_naming():
    data = w.load_workouts()
    data["sets"] = []
    report = pi.format_progress_index_report(data, days=30, now=NOW)
    assert "месяц" in report
