"""Тесты для calories.py — MET-формула оценки калорий.

Это оценка (±10-20%), не измерение — тесты проверяют математику
формулы и честное 'не посчитано' на некорректных входных данных,
не претендуют на медицинскую точность.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "..")
import calories as cal


# --- estimate_calories --------------------------------------------------

def test_estimate_calories_matches_formula():
    # calories = 45 * (3.5 * 3.5 * 75) / 200 = 206.71875 -> round 207
    result = cal.estimate_calories(75, 45)
    assert result == 207


def test_estimate_calories_custom_met():
    result = cal.estimate_calories(75, 45, met=5.0)
    assert result == round(45 * (5.0 * 3.5 * 75) / 200)


def test_estimate_calories_none_weight_returns_none():
    assert cal.estimate_calories(None, 45) is None


def test_estimate_calories_zero_weight_returns_none():
    assert cal.estimate_calories(0, 45) is None


def test_estimate_calories_negative_weight_returns_none():
    assert cal.estimate_calories(-5, 45) is None


def test_estimate_calories_none_duration_returns_none():
    assert cal.estimate_calories(75, None) is None


def test_estimate_calories_zero_duration_returns_none():
    assert cal.estimate_calories(75, 0) is None


def test_estimate_calories_scales_with_weight():
    lighter = cal.estimate_calories(60, 45)
    heavier = cal.estimate_calories(120, 45)
    # Формула линейна по весу (165.375 -> 330.75 точно x2), но round()
    # каждого числа независимо не сохраняет точное соотношение —
    # допуск ±1 покрывает погрешность округления, не ошибку формулы.
    assert abs(heavier - lighter * 2) <= 1


def test_estimate_calories_scales_with_duration():
    shorter = cal.estimate_calories(75, 30)
    longer = cal.estimate_calories(75, 60)
    assert longer == shorter * 2  # линейная зависимость от времени


# --- session_duration_minutes ---------------------------------------

def test_session_duration_computes_correctly():
    start = datetime(2026, 7, 28, 10, 0, 0, tzinfo=timezone.utc).isoformat()
    end = datetime(2026, 7, 28, 11, 15, 0, tzinfo=timezone.utc).isoformat()
    assert cal.session_duration_minutes(start, end) == 75.0


def test_session_duration_uses_now_when_no_end_given():
    start = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    duration = cal.session_duration_minutes(start)
    assert 44.5 <= duration <= 45.5  # небольшой допуск на время выполнения теста


def test_session_duration_none_when_no_start():
    assert cal.session_duration_minutes(None) is None
    assert cal.session_duration_minutes("") is None


def test_session_duration_none_on_malformed_timestamp():
    assert cal.session_duration_minutes("not-a-valid-date") is None
