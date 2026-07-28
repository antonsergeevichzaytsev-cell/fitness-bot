"""Тесты для sanity.py — проверка реалистичности веса/повторов перед
записью свободного текста (не пошагового флоу, там опечаток быть не
может — вес/повторы берутся из плана).
"""
import sys

sys.path.insert(0, "..")
import sanity
import workouts as w


# --- _find_reference_range -----------------------------------------------

def test_find_reference_uses_program_plan_when_exercise_matches():
    data = w.load_workouts()
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    ref = sanity._find_reference_range(data, normalized)
    assert ref == (45, 50, 8, 10)


def test_find_reference_falls_back_to_history_when_not_in_program():
    data = w.load_workouts()
    w.add_set(data, "сгибания на бицепс", "2026-07-20", 12.0, 12, 1)
    w.add_set(data, "сгибания на бицепс", "2026-07-27", 14.0, 10, 1)
    ref = sanity._find_reference_range(data, "сгибания на бицепс")
    assert ref == (12.0, 14.0, 10, 12)


def test_find_reference_none_when_no_plan_and_no_history():
    data = w.load_workouts()
    ref = sanity._find_reference_range(data, "совсем новое упражнение")
    assert ref is None


# --- check_weight_reps_sanity --------------------------------------------

def test_sanity_flags_obvious_weight_typo():
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 500.0, 9
    )
    assert result["suspicious"] is True
    assert result["field"] == "weight"


def test_sanity_allows_legitimate_progression():
    # +5кг сверх плана (45-50) — легитимная прогрессия, не должна триггерить
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 55.0, 9
    )
    assert result["suspicious"] is False


def test_sanity_allows_weight_within_plan_range():
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 47.5, 9
    )
    assert result["suspicious"] is False


def test_sanity_flags_obvious_reps_typo():
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 47.5, 90
    )
    assert result["suspicious"] is True
    assert result["field"] == "reps"


def test_sanity_no_reference_never_blocks():
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(data, "совсем новое упражнение", 500.0, 90)
    assert result["suspicious"] is False


def test_sanity_uses_history_when_no_program_match():
    data = w.load_workouts()
    w.add_set(data, "сгибания на бицепс", "2026-07-20", 12.0, 12, 1)
    w.add_set(data, "сгибания на бицепс", "2026-07-27", 14.0, 10, 1)
    result = sanity.check_weight_reps_sanity(data, "сгибания на бицепс", 120.0, 10)
    assert result["suspicious"] is True
    assert result["field"] == "weight"


def test_sanity_history_based_normal_weight_not_flagged():
    data = w.load_workouts()
    w.add_set(data, "сгибания на бицепс", "2026-07-20", 12.0, 12, 1)
    w.add_set(data, "сгибания на бицепс", "2026-07-27", 14.0, 10, 1)
    result = sanity.check_weight_reps_sanity(data, "сгибания на бицепс", 15.0, 10)
    assert result["suspicious"] is False


def test_sanity_none_weight_not_checked():
    # Упражнение без веса (например подтягивания) — вес не сравнивается,
    # только повторы, если план это допускает
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", None, 9
    )
    assert result["suspicious"] is False  # None вес не проверяется


def test_sanity_low_weight_typo_flagged():
    # Слишком НИЗКИЙ вес относительно плана — тоже подозрительно
    # (например, забыли ноль: '4.5' вместо '45')
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 4.5, 9
    )
    assert result["suspicious"] is True
    assert result["field"] == "weight"


def test_sanity_boundary_at_exactly_double_max_not_flagged():
    # Ровно на границе множителя (2.0x max=50 -> 100) — не флагуется,
    # флагуется только СТРОГО больше
    data = w.load_workouts()
    result = sanity.check_weight_reps_sanity(
        data, "Vertical Traction (тяга сверху к груди)", 100.0, 9
    )
    assert result["suspicious"] is False
