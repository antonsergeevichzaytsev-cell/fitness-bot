"""Тесты для strength.py — оценка 1RM по формуле Epley.
"""
import sys

sys.path.insert(0, "..")
import strength as st
import workouts as w


# --- estimate_1rm ------------------------------------------------------

def test_estimate_1rm_matches_reference_example():
    # 100кг x 5 -> 116.7кг, эталонный пример из документации формулы
    assert st.estimate_1rm(100, 5) == 116.7


def test_estimate_1rm_single_rep_equals_weight_itself():
    # При 1 повторе 1RM должен быть очень близок к самому весу
    # (1 * (1 + 1/30) = 1.033x)
    result = st.estimate_1rm(100, 1)
    assert 100 <= result <= 105


def test_estimate_1rm_none_weight_returns_none():
    assert st.estimate_1rm(None, 8) is None


def test_estimate_1rm_zero_weight_returns_none():
    assert st.estimate_1rm(0, 8) is None


def test_estimate_1rm_none_reps_returns_none():
    assert st.estimate_1rm(50, None) is None


def test_estimate_1rm_zero_reps_returns_none():
    assert st.estimate_1rm(50, 0) is None


def test_estimate_1rm_negative_weight_returns_none():
    assert st.estimate_1rm(-10, 8) is None


def test_estimate_1rm_increases_with_reps():
    lower = st.estimate_1rm(50, 3)
    higher = st.estimate_1rm(50, 10)
    assert higher > lower  # больше повторов на том же весе -> выше оценка максимума


# --- find_best_set_for_1rm --------------------------------------------

def test_find_best_set_no_history_returns_none():
    data = w.load_workouts()
    data["sets"] = []
    assert st.find_best_set_for_1rm(data, "жим лёжа") is None


def test_find_best_set_excludes_warmup():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим лёжа", "2026-07-27", 20.0, 10, 1, set_type="warmup")
    w.add_set(data, "жим лёжа", "2026-07-27", 40.0, 5, 2)
    best = st.find_best_set_for_1rm(data, "жим лёжа")
    assert best["weight_kg"] == 40.0  # разминка не выбрана, даже если её 1RM-оценка выше


def test_find_best_set_picks_highest_estimate_not_highest_weight():
    data = w.load_workouts()
    data["sets"] = []
    # 40кг x 8 -> 40*(1+8/30) = 50.67
    w.add_set(data, "жим лёжа", "2026-07-20", 40.0, 8, 1)
    # 45кг x 3 -> 45*(1+3/30) = 49.5, вес выше, но оценка НИЖЕ
    w.add_set(data, "жим лёжа", "2026-07-27", 45.0, 3, 1)
    best = st.find_best_set_for_1rm(data, "жим лёжа")
    assert best["weight_kg"] == 40.0  # выбран не самый тяжёлый вес, а лучшая оценка


def test_find_best_set_returns_correct_fields():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "присед", "2026-07-27", 60.0, 5, 1)
    best = st.find_best_set_for_1rm(data, "присед")
    assert best["weight_kg"] == 60.0
    assert best["reps"] == 5
    assert best["date"] == "2026-07-27"
    assert best["estimated_1rm"] == 70.0


# --- format_1rm_report ---------------------------------------------

def test_format_1rm_report_none_without_history():
    data = w.load_workouts()
    data["sets"] = []
    assert st.format_1rm_report(data, "жим лёжа") is None


def test_format_1rm_report_shows_estimate():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим лёжа", "2026-07-27", 45.0, 5, 1)
    report = st.format_1rm_report(data, "жим лёжа")
    assert "52.5" in report
    assert "Epley" in report


def test_format_1rm_report_warns_on_high_reps():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим лёжа", "2026-07-27", 30.0, 15, 1)
    report = st.format_1rm_report(data, "жим лёжа")
    assert "менее точна" in report


def test_format_1rm_report_no_warning_within_accuracy_range():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим лёжа", "2026-07-27", 45.0, 5, 1)
    report = st.format_1rm_report(data, "жим лёжа")
    assert "менее точна" not in report


def test_format_1rm_report_boundary_exactly_10_reps_no_warning():
    data = w.load_workouts()
    data["sets"] = []
    w.add_set(data, "жим лёжа", "2026-07-27", 40.0, 10, 1)
    report = st.format_1rm_report(data, "жим лёжа")
    assert "менее точна" not in report  # ровно 10, не > 10
