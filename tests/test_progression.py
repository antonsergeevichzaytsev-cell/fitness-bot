"""Тесты для progression.py — логика предложений прогрессии.

Особое внимание safety-гейту: и hard_block, и manual_progression_only
должны полностью блокировать автоматическое предложение. Первая версия
кода имела реальный баг здесь — manual_progression_only только менял
текст сообщения, но не блокировал само предложение — найден и
исправлен при ручном тестировании до коммита. Регрессионный тест ниже
защищает именно этот случай.
"""
import sys

sys.path.insert(0, "..")
import progression as p
import workouts as w


def _add_session(data, exercise, date, weight, reps, n_sets=1, note="", rpe=None):
    for i in range(1, n_sets + 1):
        w.add_set(data, exercise, date, weight, reps, i, note=note, rpe=rpe)


# --- safety gate: критичные тесты -----------------------------------

def test_hard_block_exercise_never_suggests():
    data = w.load_workouts()
    _add_session(data, "присед со штангой", "2026-07-20", 60.0, 12)
    _add_session(data, "присед со штангой", "2026-07-22", 60.0, 12)
    assert p.suggest_progression(data, "присед со штангой") is None


def test_barbell_bench_press_never_suggests():
    data = w.load_workouts()
    _add_session(data, "жим штанги лёжа", "2026-07-20", 80.0, 12)
    _add_session(data, "жим штанги лёжа", "2026-07-22", 80.0, 12)
    assert p.suggest_progression(data, "жим штанги лёжа") is None


def test_manual_progression_only_never_auto_suggests():
    # Регрессия: баг найден при ручном тестировании 28.07.2026 —
    # manual_progression_only давал предложение с добавленным текстом
    # предупреждения вместо полной блокировки. Правило
    # safety_constraints.json: "прогрессия НЕ предлагается автоматически,
    # только по явному запросу" — не "предлагается, но с оговоркой".
    data = w.load_workouts()
    _add_session(data, "жим ногами", "2026-07-20", 150.0, 12)
    _add_session(data, "жим ногами", "2026-07-22", 150.0, 12)
    assert p.suggest_progression(data, "жим ногами") is None


def test_lunges_with_weight_caution_never_auto_suggests():
    data = w.load_workouts()
    _add_session(data, "выпады с весом", "2026-07-20", 20.0, 12)
    _add_session(data, "выпады с весом", "2026-07-22", 20.0, 12)
    assert p.suggest_progression(data, "выпады с весом") is None


# --- нормальная прогрессия: safe-упражнения -------------------------

def test_suggests_weight_increase_when_all_sessions_at_top_of_range():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 12)
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12)
    result = p.suggest_progression(data, "жим лёжа гантели")
    assert result is not None
    assert result["action"] == "increase_weight"
    assert result["suggested_weight_kg"] == 32.5
    assert result["suggested_reps"] == 8  # низ диапазона после роста веса


def test_no_suggestion_when_reps_below_top_of_range():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 9)
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 9)
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_no_suggestion_when_only_some_sets_at_top():
    data = w.load_workouts()
    for date in ["2026-07-20", "2026-07-22"]:
        w.add_set(data, "жим лёжа гантели", date, 30.0, 12, 1)
        w.add_set(data, "жим лёжа гантели", date, 30.0, 10, 2)  # второй сет не дотянул
    assert p.suggest_progression(data, "жим лёжа гантели") is None


# --- сигналы трудности блокируют прогрессию --------------------------

def test_no_suggestion_when_high_rpe_present():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 12, rpe=9)
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12)
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_no_suggestion_when_note_mentions_difficulty():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 12, note="последний подход тяжело пошёл")
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12)
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_no_suggestion_when_note_mentions_struggle_keyword():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 12, note="через силу доделал")
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12)
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_suggestion_ok_with_moderate_rpe():
    # RPE ниже порога — не блокирует
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-20", 30.0, 12, rpe=7)
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12, rpe=7)
    result = p.suggest_progression(data, "жим лёжа гантели")
    assert result is not None


# --- недостаточно данных -----------------------------------------------

def test_no_suggestion_with_only_one_session():
    data = w.load_workouts()
    _add_session(data, "жим лёжа гантели", "2026-07-22", 30.0, 12)
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_no_suggestion_with_no_history_at_all():
    data = w.load_workouts()
    assert p.suggest_progression(data, "жим лёжа гантели") is None


def test_no_suggestion_for_bodyweight_exercise_without_weight():
    # Упражнение без веса (например, подтягивания без отягощения) —
    # прогрессия по весу не применима этим механизмом
    data = w.load_workouts()
    w.add_set(data, "подтягивания", "2026-07-20", None, 12, 1)
    w.add_set(data, "подтягивания", "2026-07-22", None, 12, 1)
    assert p.suggest_progression(data, "подтягивания") is None


# --- format_suggestion_message ------------------------------------------

def test_format_suggestion_message_includes_key_info():
    suggestion = {
        "action": "increase_weight",
        "reasoning": "тестовое обоснование",
        "suggested_weight_kg": 32.5,
        "suggested_reps": 8,
    }
    text = p.format_suggestion_message("жим лёжа гантели", suggestion)
    assert "жим лёжа гантели" in text
    assert "тестовое обоснование" in text
    assert "32.5" in text
    assert "8" in text
