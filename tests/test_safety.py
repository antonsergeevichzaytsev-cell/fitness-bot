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


def test_leg_extension_blocked():
    result = safety.check_exercise("разгибание ног сидя 40х12")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "leg_extension"


def test_romanian_deadlift_barbell_blocked():
    result = safety.check_exercise("румынская тяга со штангой 60х8")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "romanian_deadlift_barbell"


def test_running_blocked():
    result = safety.check_exercise("бег на дорожке 20 минут")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "high_impact_cardio"


def test_jumping_blocked():
    result = safety.check_exercise("прыжки на скакалке")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "high_impact_cardio"


def test_hack_squat_blocked():
    result = safety.check_exercise("гакк-присед 80х10")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "barbell_squat"


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


def test_leg_press_is_hard_blocked():
    # ОБНОВЛЕНО 28.07.2026 (получен Anton_Training_Program.docx): день
    # ног целиком исключён из программы ('колени, решение ноги дороже'),
    # leg press — hard_block, не caution, как было в первой версии
    # constraints (составленной по устной информации до документа).
    result = safety.check_exercise("жим ногами в тренажёре 150х12")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "leg_press"


def test_unrelated_exercises_ok():
    for name in ["тяга верхнего блока", "подтягивания", "жим гантелей стоя",
                 "разводка гантелей", "тяга штанги в наклоне"]:
        result = safety.check_exercise(name)
        assert result["status"] == "ok", f"false positive on {name!r}: {result}"


def test_program_exercises_not_falsely_blocked():
    # Реальные упражнения из всех 4 дней программы (Anton_Training_
    # Program.docx) — ни одно не должно совпасть с расширенным
    # стоп-листом. Это самая важная проверка: ложное срабатывание
    # здесь заблокирует легитимное упражнение программы.
    program_exercises = [
        "Vertical Traction тяга сверху к груди",
        "Low Row нейтральным хватом",
        "Single-arm DB Row гантель одной рукой",
        "Lat Pulldown широким хватом",
        "Reverse Pec Deck задняя дельта",
        "Biceps Curl Machine",
        "Hammer Curl молотки",
        "Hyperextension с весом",
        "Incline DB Press наклонный жим 30",
        "Hammer Iso-Lateral Chest Press",
        "Pec Deck бабочка",
        "Shoulder Press DB жим сидя",
        "Lateral Raise махи в стороны",
        "Triceps Rope Pushdown с канатом",
        "Overhead Triceps Extension фр жим сидя",
        "High Row тяга к верху груди",
        "Single-arm Cable Row одной рукой",
        "Straight-arm Pulldown прямыми руками",
        "Face Pull тяга к лицу с канатом",
        "Preacher Curl скамья Скотта",
        "Cable Curl с нижнего блока",
        "Cable Crunch скручивания на блоке",
        "Pallof Press антиротация",
        "Flat DB Press жим лёжа горизонтально",
        "Cable Crossover сверху-вниз",
        "Assisted Dips брусья с противовесом",
        "Rear Delt Fly обратные разводки",
        "Cable Lateral Raise нижний блок",
        "Close-grip Chest Press узкий жим",
        "Rope Pushdown трицепс другой угол",
    ]
    for name in program_exercises:
        result = safety.check_exercise(name)
        assert result["status"] == "ok", f"false positive on {name!r}: {result}"


# --- hard_block: выпады (обновлено из документа программы) ---------------

def test_lunges_hard_blocked():
    # ОБНОВЛЕНО 28.07.2026: документ программы — стоп-лист прямо
    # называет выпады, не caution-категорию.
    result = safety.check_exercise("выпады с весом 20х10")
    assert result["status"] == "hard_block"
    assert result["pattern"] == "lunges"


def test_bulgarian_split_squat_hard_blocked():
    result = safety.check_exercise("болгарские выпады с весом")
    assert result["status"] == "hard_block"


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


def test_get_profile_returns_target_from_program():
    profile = safety.get_profile()
    assert profile["target_weight_kg"] == 106
    assert profile["target_date"] == "2027-01"
