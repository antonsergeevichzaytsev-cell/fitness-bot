"""Тесты для program.py — определение дня по расписанию, доступ к плану.

Расписание жёстко зафиксировано документом (Anton_Training_Program.docx):
Пн=1, Ср=2, Пт=3, Сб=4, остальное — отдых. Не эвристика, точное
соответствие — тесты проверяют все 7 дней недели явно.
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "..")
import program as p
import safety


# --- today_day_id ------------------------------------------------------

def test_monday_is_day_1():
    monday = datetime(2026, 7, 27, tzinfo=timezone.utc)  # реальный понедельник
    assert p.today_day_id(monday) == "1"


def test_tuesday_is_rest():
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)  # реальный вторник (сегодня)
    assert p.today_day_id(tuesday) is None


def test_wednesday_is_day_2():
    wednesday = datetime(2026, 7, 29, tzinfo=timezone.utc)
    assert p.today_day_id(wednesday) == "2"


def test_thursday_is_rest():
    thursday = datetime(2026, 7, 30, tzinfo=timezone.utc)
    assert p.today_day_id(thursday) is None


def test_friday_is_day_3():
    friday = datetime(2026, 7, 31, tzinfo=timezone.utc)
    assert p.today_day_id(friday) == "3"


def test_saturday_is_day_4():
    saturday = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert p.today_day_id(saturday) == "4"


def test_sunday_is_rest():
    sunday = datetime(2026, 8, 2, tzinfo=timezone.utc)
    assert p.today_day_id(sunday) is None


# --- get_day_plan / get_exercise ----------------------------------------

def test_get_day_plan_returns_correct_day():
    day = p.get_day_plan("1")
    assert day["name"] == "Спина + Задняя дельта + Бицепс"
    assert day["weekday"] == "monday"
    assert len(day["exercises"]) == 8


def test_get_day_plan_unknown_id_returns_none():
    assert p.get_day_plan("99") is None


def test_get_exercise_returns_correct_order():
    ex = p.get_exercise("1", 1)
    assert ex["name"] == "Vertical Traction (тяга сверху к груди)"
    assert ex["sets"] == 4


def test_get_exercise_unknown_order_returns_none():
    assert p.get_exercise("1", 99) is None


def test_get_exercise_unknown_day_returns_none():
    assert p.get_exercise("99", 1) is None


# --- format_exercise_line -------------------------------------------------

def test_format_exercise_line_weight_range():
    ex = p.get_exercise("1", 1)  # Vertical Traction, 45-50кг
    line = p.format_exercise_line(ex)
    assert "45-50кг" in line


def test_format_exercise_line_fixed_weight_no_ugly_range():
    ex = p.get_exercise("1", 7)  # Hammer Curl, 12-12 в данных -> "12кг"
    line = p.format_exercise_line(ex)
    assert "12кг" in line
    assert "12-12кг" not in line


def test_format_exercise_line_by_feel_weight():
    ex = p.get_exercise("2", 2)  # Hammer Iso-Lateral Chest Press, null/null
    line = p.format_exercise_line(ex)
    assert "по ощущению" in line


def test_format_exercise_line_per_side():
    ex = p.get_exercise("3", 2)  # Single-arm Cable Row, per_side=true
    line = p.format_exercise_line(ex)
    assert "/сторона" in line


def test_format_exercise_line_reps_range_vs_fixed():
    ex_range = p.get_exercise("1", 1)  # 8-10
    ex_fixed = p.get_exercise("2", 7)  # reps_min==reps_max==12
    assert "8-10" in p.format_exercise_line(ex_range)
    line_fixed = p.format_exercise_line(ex_fixed)
    assert "12-12" not in line_fixed


# --- format_day_plan -----------------------------------------------------

def test_format_day_plan_includes_all_exercises():
    plan = p.format_day_plan("1")
    assert "День 1" in plan
    for ex in p.get_day_plan("1")["exercises"]:
        assert ex["name"] in plan


def test_format_day_plan_unknown_day_returns_none():
    assert p.format_day_plan("99") is None


# --- критичная интеграция: программа x safety -----------------------------

def test_all_program_exercises_pass_safety_check():
    # КРИТИЧНО: если хоть одно название упражнения в training_program.json
    # случайно совпадёт с паттерном стоп-листа, safety заблокирует
    # ЛЕГИТИМНОЕ упражнение программы — это должно быть невозможно, раз
    # программа и стоп-лист взяты из одного документа и не должны
    # противоречить друг другу.
    program = p.load_program()
    total = 0
    for day_id, day in program["days"].items():
        for ex in day["exercises"]:
            result = safety.check_exercise(ex["name"])
            assert result["status"] == "ok", (
                f"День {day_id}, {ex['name']!r} заблокировано: {result}"
            )
            total += 1
    assert total == 31  # 8+7+8+8, сверка с документом
