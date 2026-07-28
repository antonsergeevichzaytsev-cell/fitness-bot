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
import workouts as w


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


# --- _set_status --------------------------------------------------------

def test_set_status_on_plan():
    ex = p.get_exercise("1", 1)  # 8-10 reps, 45-50кг
    emoji, status = p._set_status(ex, 47.5, 9)
    assert status == "по плану"
    assert emoji == "\u2705"


def test_set_status_above_plan_by_weight():
    ex = p.get_exercise("1", 1)
    emoji, status = p._set_status(ex, 52.5, 10)  # вес выше max
    assert status == "сверх плана"
    assert emoji == "\U0001f4c8"


def test_set_status_above_plan_by_reps():
    ex = p.get_exercise("1", 1)
    emoji, status = p._set_status(ex, 47.5, 12)  # повторы выше max
    assert status == "сверх плана"


def test_set_status_below_plan_by_weight():
    ex = p.get_exercise("1", 1)
    emoji, status = p._set_status(ex, 40.0, 9)  # вес ниже min
    assert status == "ниже плана"
    assert emoji == "\U0001f4c9"


def test_set_status_below_plan_by_reps():
    ex = p.get_exercise("1", 1)
    emoji, status = p._set_status(ex, 47.5, 6)  # повторы ниже min
    assert status == "ниже плана"


def test_set_status_by_feel_weight_only_compares_reps():
    ex = p.get_exercise("2", 2)  # Hammer Iso-Lateral, по ощущению
    emoji_ok, status_ok = p._set_status(ex, 999.0, 11)  # любой вес ок
    assert status_ok == "по плану"
    emoji_bad, status_bad = p._set_status(ex, 999.0, 6)  # только повторы вне плана
    assert status_bad == "повторы вне плана"


def test_set_status_exact_boundary_counts_as_on_plan():
    ex = p.get_exercise("1", 1)  # 45-50кг, 8-10 reps
    emoji, status = p._set_status(ex, 45.0, 8)  # ровно нижняя граница
    assert status == "по плану"
    emoji2, status2 = p._set_status(ex, 50.0, 10)  # ровно верхняя граница
    assert status2 == "по плану"


# --- format_exercise_plan_vs_fact ---------------------------------------

def test_format_plan_vs_fact_includes_exercise_name_and_plan():
    ex = p.get_exercise("1", 1)
    report = p.format_exercise_plan_vs_fact(ex, [])
    assert ex["name"] in report
    assert "8-10" in report
    assert "45-50кг" in report


def test_format_plan_vs_fact_lists_each_set():
    ex = p.get_exercise("1", 1)
    actual_sets = [
        {"set_number": 1, "weight_kg": 47.5, "reps": 9},
        {"set_number": 2, "weight_kg": 50.0, "reps": 10},
    ]
    report = p.format_exercise_plan_vs_fact(ex, actual_sets)
    assert "Подход 1" in report
    assert "Подход 2" in report
    assert "47.5кг" in report
    assert "50.0кг" in report


def test_format_plan_vs_fact_shows_status_emoji_per_set():
    ex = p.get_exercise("1", 1)
    actual_sets = [
        {"set_number": 1, "weight_kg": 47.5, "reps": 9},   # по плану
        {"set_number": 2, "weight_kg": 52.5, "reps": 10},  # сверх
    ]
    report = p.format_exercise_plan_vs_fact(ex, actual_sets)
    assert "\u2705" in report
    assert "\U0001f4c8" in report


def test_format_plan_vs_fact_handles_no_weight_set():
    ex = p.get_exercise("1", 1)
    actual_sets = [{"set_number": 1, "weight_kg": None, "reps": 9}]
    report = p.format_exercise_plan_vs_fact(ex, actual_sets)
    assert "б/в" in report  # без веса, не падает на None


# --- format_day_plan_with_targets ----------------------------------------
# Найдено 28.07.2026: target прогрессии сохранялся через w.set_target
# при подтверждении, но НИКОГДА не отображался в плане и не применялся
# при показе — format_day_plan была чистой read-only функцией, не знающей
# о состоянии workouts.json. Этот блок тестов защищает фикс.

def test_format_day_plan_with_targets_shows_static_weight_when_no_target():
    data = w.load_workouts()
    plan = p.format_day_plan_with_targets("1", data)
    assert "45-50кг" in plan  # без target — обычный план из программы


def test_format_day_plan_with_targets_shows_confirmed_target_weight():
    data = w.load_workouts()
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)
    plan = p.format_day_plan_with_targets("1", data)
    assert "52.5кг" in plan
    assert "45-50кг" not in plan  # старый диапазон больше не показывается


def test_format_day_plan_with_targets_only_affects_targeted_exercise():
    data = w.load_workouts()
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)
    plan = p.format_day_plan_with_targets("1", data)
    assert "65-70кг" in plan  # Low Row без target — план не тронут


def test_format_day_plan_with_targets_unknown_day_returns_none():
    data = w.load_workouts()
    assert p.format_day_plan_with_targets("99", data) is None



# --- format_warmup / format_cooldown --------------------------------

def test_format_warmup_includes_all_steps():
    warmup_text = p.format_warmup()
    assert "Разминка" in warmup_text
    assert "Кардио разогрев" in warmup_text
    assert "Суставы" in warmup_text
    assert "Динамика" in warmup_text
    assert "Подводящий подход" in warmup_text


def test_format_warmup_shows_duration_range():
    warmup_text = p.format_warmup()
    assert "12-15" in warmup_text


def test_format_warmup_none_without_warmup_field():
    fake_program = {"days": {}}  # без поля 'warmup'
    assert p.format_warmup(fake_program) is None


def test_format_cooldown_returns_day_specific_text():
    cooldown_text = p.format_cooldown("1")
    assert "турнике" in cooldown_text  # специфично для дня 1


def test_format_cooldown_different_per_day():
    cd1 = p.format_cooldown("1")
    cd2 = p.format_cooldown("2")
    assert cd1 != cd2


def test_format_cooldown_none_for_unknown_day():
    assert p.format_cooldown("99") is None


# --- get_phase_info / apply_phase_modifier -------------------------------

def test_get_phase_info_returns_known_phases():
    for phase_id in ["strength", "volume", "deficit"]:
        phase = p.get_phase_info(phase_id)
        assert phase is not None
        assert "reps_multiplier" in phase


def test_get_phase_info_unknown_returns_none():
    assert p.get_phase_info("unknown_phase") is None


def test_apply_phase_modifier_volume_no_change():
    ex = p.get_exercise("1", 1)
    result = p.apply_phase_modifier(ex, "volume")
    assert result["reps_min"] == ex["reps_min"]
    assert result["reps_max"] == ex["reps_max"]
    assert result["weight_min_kg"] == ex["weight_min_kg"]
    assert result["rest_sec"] == ex["rest_sec"]


def test_apply_phase_modifier_strength_lowers_reps_raises_weight():
    ex = p.get_exercise("1", 1)  # 8-10 reps, 45-50кг
    result = p.apply_phase_modifier(ex, "strength")
    assert result["reps_min"] < ex["reps_min"]
    assert result["reps_max"] < ex["reps_max"]
    assert result["weight_min_kg"] > ex["weight_min_kg"]
    assert result["weight_max_kg"] > ex["weight_max_kg"]


def test_apply_phase_modifier_deficit_raises_reps_lowers_weight_and_rest():
    ex = p.get_exercise("1", 1)
    result = p.apply_phase_modifier(ex, "deficit")
    assert result["reps_min"] > ex["reps_min"]
    assert result["reps_max"] > ex["reps_max"]
    assert result["weight_min_kg"] < ex["weight_min_kg"]
    assert result["weight_max_kg"] < ex["weight_max_kg"]
    assert result["rest_sec"] < ex["rest_sec"]


def test_apply_phase_modifier_unknown_phase_no_change():
    ex = p.get_exercise("1", 1)
    result = p.apply_phase_modifier(ex, "unknown_phase")
    assert result["reps_min"] == ex["reps_min"]
    assert result["weight_min_kg"] == ex["weight_min_kg"]


def test_apply_phase_modifier_does_not_mutate_original():
    ex = p.get_exercise("1", 1)
    before = dict(ex)
    p.apply_phase_modifier(ex, "strength")
    after = p.get_exercise("1", 1)
    assert before == after


def test_apply_phase_modifier_none_weight_stays_none():
    ex = p.get_exercise("2", 2)  # Hammer Iso-Lateral, по ощущению
    result = p.apply_phase_modifier(ex, "strength")
    assert result["weight_min_kg"] is None
    assert result["weight_max_kg"] is None


def test_apply_phase_modifier_weight_rounds_to_half_kg():
    ex = p.get_exercise("1", 1)  # 45-50кг
    result = p.apply_phase_modifier(ex, "strength")  # x1.1
    # 45*1.1=49.5, 50*1.1=55.0 — оба уже кратны 0.5
    assert result["weight_min_kg"] % 0.5 == 0
    assert result["weight_max_kg"] % 0.5 == 0


def test_apply_phase_modifier_reps_never_below_one():
    # Экстремальный тест: если бы множитель был очень маленьким,
    # повторы не должны уйти в 0 или отрицательные
    ex = {"reps_min": 8, "reps_max": 10, "weight_min_kg": 45, "weight_max_kg": 50, "rest_sec": 90}
    fake_program = {"phases": {"extreme": {
        "reps_multiplier": 0.01, "weight_multiplier": 1.0, "rest_multiplier": 1.0
    }}}
    result = p.apply_phase_modifier(ex, "extreme", fake_program)
    assert result["reps_min"] >= 1
    assert result["reps_max"] >= result["reps_min"]


# --- format_day_plan_with_targets: интеграция с фазой периодизации -------

def test_format_day_plan_with_targets_applies_strength_phase():
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-07-28")
    plan = p.format_day_plan_with_targets("1", data)
    assert "45-50кг" not in plan  # старый диапазон не должен остаться
    assert "6-7" in plan  # модифицированные повторы


def test_format_day_plan_with_targets_applies_deficit_phase():
    data = w.load_workouts()
    w.set_active_phase(data, "deficit", "2026-07-28")
    plan = p.format_day_plan_with_targets("1", data)
    assert "45-50кг" not in plan


def test_format_day_plan_with_targets_volume_phase_shows_static():
    data = w.load_workouts()
    # active_phase по умолчанию 'volume'
    plan = p.format_day_plan_with_targets("1", data)
    assert "45-50кг" in plan


def test_format_day_plan_with_targets_target_beats_phase_in_display():
    # КРИТИЧНО: тот же приоритет, что в session.current_exercise_info,
    # должен соблюдаться и в отображении плана, не только в логике флоу
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-07-28")
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)
    plan = p.format_day_plan_with_targets("1", data)
    # Изолируем строку именно Vertical Traction — 49.5 (=45x1.1) может
    # легитимно встретиться в фазово-модифицированном весе ДРУГОГО
    # упражнения дня, проверка по всему тексту была бы ложноположительной
    vt_line_start = plan.find("Vertical Traction")
    vt_line_end = plan.find("\n\n", vt_line_start)
    vt_section = plan[vt_line_start:vt_line_end]
    assert "52.5кг" in vt_section  # точный target, не модифицированный фазой
    assert "8-10" in vt_section  # повторы тоже НЕ модифицированы (target не трогает reps плана — они из ex)
