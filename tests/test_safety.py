"""Тесты для safety.py — критичная проверка медицинских ограничений.

Это последняя линия защиты перед тем, как бот предложит прогрессию
веса по запрещённому упражнению (присед со штангой — травма колена;
жим штанги лёжа — исключён явно 28.07.2026). Тесты здесь особенно
важны: любая регрессия тут не "неверный совет", а потенциальный риск
травмы, если бот тихо начнёт предлагать вес по запрещённому паттерну.
"""
import sys

sys.path.insert(0, "..")
import safety


# --- hard_block: запрещённые категорически -----------------------------

def test_barbell_squat_blocked():
    result = safety.check_exercise("присед со штангой 100х5")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "barbell_squat"


def test_barbell_squat_english_blocked():
    result = safety.check_exercise("back squat 90kg x5")
    assert result["status"] == "hard_block"


def test_barbell_bench_press_blocked():
    result = safety.check_exercise("жим штанги лёжа 80х8")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "barbell_bench_press"


def test_barbell_squat_blocked_case_insensitive():
    result = safety.check_exercise("ПРИСЕД СО ШТАНГОЙ")
    assert result["status"] == "hard_block"


# --- ok: похожие, но НЕ запрещённые упражнения (ложноположительный тест) ---
# Это самая важная категория тестов: если матчинг слишком широкий,
# бот заблокирует безопасные упражнения, которые Антон реально делает
# (жим лёжа гантели явно разрешён 28.07.2026).

def test_dumbbell_bench_press_allowed():
    result = safety.check_exercise("жим лёжа гантели 30х10")
    assert result["status"] == "ok"


def test_machine_bench_press_allowed():
    result = safety.check_exercise("жим в тренажёре 40х12")
    assert result["status"] == "ok"


def test_leg_press_is_caution_not_blocked():
    # Жим ногами — не запрет, а caution (осевая нагрузка на колени,
    # но не запрещённый паттерн категорически)
    result = safety.check_exercise("жим ногами в тренажёре 150х12")
    assert result["status"] == "manual_progression_only"


def test_unrelated_exercises_ok():
    for name in ["тяга верхнего блока", "подтягивания", "жим гантелей стоя",
                 "разводка гантелей", "тяга штанги в наклоне"]:
        result = safety.check_exercise(name)
        assert result["status"] == "ok", f"false positive on {name!r}: {result}"


# --- manual_progression_only: осторожные, не запрещены полностью ---------

def test_lunges_with_weight_caution():
    result = safety.check_exercise("выпады с весом 20х10")
    assert result["status"] == "manual_progression_only"
    assert result["pattern"] == "knee_axial_load"


def test_bulgarian_split_squat_weighted_caution():
    result = safety.check_exercise("болгарские выпады с весом")
    assert result["status"] == "manual_progression_only"


# --- edge cases ------------------------------------------------------------

def test_empty_string_ok():
    assert safety.check_exercise("") == {"status": "ok"}


def test_none_ok():
    assert safety.check_exercise(None) == {"status": "ok"}


# --- get_profile -------------------------------------------------------

def test_get_profile_returns_height_and_weight():
    profile = safety.get_profile()
    assert profile["height_cm"] == 192
    assert profile["weight_kg"] == 121
