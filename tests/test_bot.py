"""Тесты для bot.py — сборка parser + workouts + progression + safety
через обработчики сообщений и callback-кнопок.

Реальная сеть (Telegram getUpdates/sendMessage, DeepSeek) не вызывается
ни в одном тесте — тестируем оркестрацию модулей, не сетевой слой (тот
уже покрыт test_parser.py через net.urlopen_retry).
"""
import os
import sys
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")

sys.path.insert(0, "..")
import bot
import program as prog
import session as sess
import workouts as w


# --- handle_workout_message: uncertain -----------------------------------

def test_handle_uncertain_returns_question_without_recording():
    data = w.load_workouts()
    fake_parsed = {"uncertain": True, "question": "Какой был вес?", "sets": []}
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        results = bot.handle_workout_message("сделал подходы", data)
    assert results == ["Какой был вес?"]
    assert data["sets"] == []  # ничего не записано при неясности


def test_handle_empty_sets_asks_to_rephrase():
    data = w.load_workouts()
    fake_parsed = {"uncertain": False, "sets": []}
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        results = bot.handle_workout_message("что-то непонятное", data)
    assert len(results) == 1
    assert "переформулировать" in results[0]


# --- handle_workout_message: успешная запись ------------------------

def test_handle_records_all_sets():
    data = w.load_workouts()
    fake_parsed = {
        "uncertain": False,
        "sets": [
            {"exercise": "присед", "weight_kg": 50.0, "reps": 8, "rpe": None, "note": ""},
            {"exercise": "присед", "weight_kg": 50.0, "reps": 8, "rpe": None, "note": ""},
        ],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        bot.handle_workout_message("присед 50 на 8 два подхода", data)
    assert len(data["sets"]) == 2
    assert data["sets"][0]["set_number"] == 1
    assert data["sets"][1]["set_number"] == 2


def test_handle_confirmation_message_lists_exercises():
    data = w.load_workouts()
    fake_parsed = {
        "uncertain": False,
        "sets": [{"exercise": "присед", "weight_kg": 50.0, "reps": 8, "rpe": None, "note": ""}],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        results = bot.handle_workout_message("присед 50 на 8", data)
    assert "присед" in results[0]
    assert "50.0" in results[0]


def test_handle_records_safety_status_on_entry():
    data = w.load_workouts()
    fake_parsed = {
        "uncertain": False,
        "sets": [{"exercise": "присед со штангой", "weight_kg": 60.0, "reps": 5, "rpe": None, "note": ""}],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        bot.handle_workout_message("присед со штангой 60 на 5", data)
    assert data["sets"][0]["safety_status"] == "hard_block"


def test_handle_still_records_banned_exercise_but_no_suggestion():
    # ВАЖНО: safety блокирует только АВТОМАТИЧЕСКОЕ ПРЕДЛОЖЕНИЕ прогрессии,
    # не саму запись факта — Антон мог реально сделать упражнение,
    # это его выбор и его тело, бот не должен молчать/врать про историю.
    data = w.load_workouts()
    fake_parsed = {
        "uncertain": False,
        "sets": [{"exercise": "присед со штангой", "weight_kg": 60.0, "reps": 12, "rpe": None, "note": ""}],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        w.add_set(data, "присед со штангой", "2026-07-20", 60.0, 12, 1)
        results = bot.handle_workout_message("присед со штангой 60 на 12", data)
    assert len(data["sets"]) == 2  # запись прошла
    # ни одного tuple (suggestion) среди results — только подтверждение записи
    assert all(not isinstance(r, tuple) for r in results)


# --- handle_workout_message: предложение прогрессии -----------------

def test_handle_generates_suggestion_when_progression_criteria_met():
    data = w.load_workouts()
    w.add_set(data, "жим лёжа гантели", "2026-07-26", 30.0, 12, 1)
    fake_parsed = {
        "uncertain": False,
        "sets": [{"exercise": "жим лёжа гантели", "weight_kg": 30.0, "reps": 12, "rpe": None, "note": ""}],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        results = bot.handle_workout_message("жим лежа гантелями 30 на 12", data)
    suggestion_items = [r for r in results if isinstance(r, tuple)]
    assert len(suggestion_items) == 1
    assert len(data["pending_suggestions"]) == 1


def test_handle_no_suggestion_with_insufficient_history():
    data = w.load_workouts()
    fake_parsed = {
        "uncertain": False,
        "sets": [{"exercise": "жим лёжа гантели", "weight_kg": 30.0, "reps": 12, "rpe": None, "note": ""}],
    }
    with mock.patch("bot.parser.parse_workout_text", return_value=fake_parsed):
        results = bot.handle_workout_message("жим лежа гантелями 30 на 12", data)
    assert all(not isinstance(r, tuple) for r in results)
    assert data.get("pending_suggestions", []) == []


# --- handle_callback -----------------------------------------------------

def test_callback_confirm_sets_target():
    data = w.load_workouts()
    data["pending_suggestions"] = [{
        "id": "sugg_test", "exercise": "присед",
        "suggested_weight_kg": 52.5, "suggested_reps": 8,
        "reasoning": "x", "message_id": None, "status": "pending",
        "created_ts": "2026-07-28T00:00:00",
    }]
    result = bot.handle_callback("sugg:confirm:sugg_test", data)
    assert "Принято" in result
    assert w.get_target(data, "присед")["weight_kg"] == 52.5
    assert data["pending_suggestions"][0]["status"] == "confirmed"


def test_callback_reject_does_not_set_target():
    data = w.load_workouts()
    data["pending_suggestions"] = [{
        "id": "sugg_test", "exercise": "присед",
        "suggested_weight_kg": 52.5, "suggested_reps": 8,
        "reasoning": "x", "message_id": None, "status": "pending",
        "created_ts": "2026-07-28T00:00:00",
    }]
    result = bot.handle_callback("sugg:reject:sugg_test", data)
    assert result is not None
    assert w.get_target(data, "присед") is None
    assert data["pending_suggestions"][0]["status"] == "rejected"


def test_callback_already_processed_returns_message():
    data = w.load_workouts()
    data["pending_suggestions"] = [{
        "id": "sugg_test", "exercise": "присед",
        "suggested_weight_kg": 52.5, "suggested_reps": 8,
        "reasoning": "x", "message_id": None, "status": "confirmed",
        "created_ts": "2026-07-28T00:00:00",
    }]
    result = bot.handle_callback("sugg:confirm:sugg_test", data)
    assert "Уже обработано" in result


def test_callback_unknown_suggestion_id():
    data = w.load_workouts()
    result = bot.handle_callback("sugg:confirm:nonexistent", data)
    assert "неактуально" in result


def test_callback_ignores_non_matching_format():
    data = w.load_workouts()
    assert bot.handle_callback("something:else", data) is None
    assert bot.handle_callback("sugg:onlytwo", data) is None
    assert bot.handle_callback("", data) is None


# --- handle_session_start / handle_set_confirmation ------------------
# Пошаговый флоу: "начал" -> план на день -> "взял" x N -> "закончил".

def test_session_start_rest_day_no_program():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 28, tzinfo=timezone.utc)  # вторник, отдых
        result = bot.handle_session_start(data)
    assert "отдых" in result.lower()


def test_session_start_training_day_asks_for_weight_first():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)  # понедельник
        result = bot.handle_session_start(data)
    # План НЕ показывается сразу — сначала вопрос о весе
    assert "весишь" in result.lower()
    assert "День 1" not in result


def test_session_start_already_active_while_awaiting_weight():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
        result = bot.handle_session_start(data)  # повторный "начал", вес ещё не дан
    assert "жду твой вес" in result.lower()


def test_session_start_already_active_after_weight_given():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
        bot.handle_weight_answer(data, "121")
        result = bot.handle_session_start(data)  # повторный "начал", вес уже дан
    assert "уже идёт" in result.lower()


# --- handle_weight_answer -------------------------------------------

def test_weight_answer_shows_plan_after_valid_number():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    result = bot.handle_weight_answer(data, "121.5")
    assert "День 1" in result
    assert "Vertical Traction" in result
    assert "взял" in result
    assert data["active_session"]["body_weight_kg"] == 121.5


def test_weight_answer_saves_body_weight():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    assert data["active_session"]["body_weight_kg"] == 121.0
    assert data["active_session"]["awaiting_weight_input"] is False


def test_weight_answer_unparseable_text_asks_again():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    result = bot.handle_weight_answer(data, "не помню сколько")
    assert "не понял" in result.lower()
    assert data["active_session"]["body_weight_kg"] is None
    assert data["active_session"]["awaiting_weight_input"] is True  # флаг не снят


def test_main_loop_routes_to_weight_handler_before_other_checks():
    # Косвенная проверка порядка: пока is_awaiting_weight_input=True,
    # даже сообщение, похожее на короткую команду ('взял'), должно
    # обрабатываться как ответ на вопрос о весе, не как подтверждение
    # подхода — потому что numeric text вроде "121" не совпадает с
    # SET_DONE_KEYWORDS, но для строгости порядок проверок в main()
    # ставит is_awaiting_weight_input первым.
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    assert sess.is_awaiting_weight_input(data) is True


def test_set_confirmation_without_active_session():
    data = w.load_workouts()
    result = bot.handle_set_confirmation(data)
    assert isinstance(result, list)
    assert "нет активной тренировки" in result[0].lower()


def test_set_confirmation_records_and_shows_rest_timer():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    result = bot.handle_set_confirmation(data)
    combined = "\n".join(result)
    assert "Подход 1/4" in combined
    assert "90 сек" in combined
    assert len(data["sets"]) == 1


def test_set_confirmation_transitions_to_next_exercise():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    for _ in range(4):  # упражнение 1 = 4 подхода
        result = bot.handle_set_confirmation(data)
    # На 4-м (последнем) подходе exercise_complete=True -> два сообщения:
    # план/факт по завершённому упражнению + переход к следующему
    assert len(result) == 2
    assert "Vertical Traction" in result[0]  # план/факт отчёт
    assert "по плану" in result[0]  # все подходы сделаны точно по плану (по умолчанию max)
    assert "Следующее упражнение" in result[1]
    assert "Low Row" in result[1]


def test_set_confirmation_day_complete_message():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    total_sets = sum(ex["sets"] for ex in prog.get_day_plan("1")["exercises"])
    for _ in range(total_sets):
        result = bot.handle_set_confirmation(data)
    combined = "\n".join(result).lower()
    assert "завершена" in combined
    assert "закончил" in combined


def test_set_confirmation_exercise_complete_shows_plan_vs_fact():
    # Отдельный прямой тест на саму фичу плана/факта (не только косвенно
    # через transition-тест выше) — проверяет структуру и содержание
    # первого сообщения при завершении упражнения.
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    for _ in range(4):
        result = bot.handle_set_confirmation(data)
    plan_vs_fact = result[0]
    assert "план: 4 x 8-10, 45-50кг" in plan_vs_fact
    assert "Подход 1" in plan_vs_fact
    assert "Подход 4" in plan_vs_fact
    assert plan_vs_fact.count("\u2705") == 4  # все 4 подхода по плану


# --- handle_extend_rest -------------------------------------------------

def test_handle_extend_rest_without_active_rest():
    data = w.load_workouts()
    result = bot.handle_extend_rest(data, "устал")
    assert "нет активного отдыха" in result.lower()


def test_handle_extend_rest_with_explicit_seconds():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    bot.handle_set_confirmation(data)
    result = bot.handle_extend_rest(data, "продли на 45")
    assert "45 сек" in result
    assert "продлён" in result.lower()


def test_handle_extend_rest_with_default_when_no_number():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    bot.handle_set_confirmation(data)
    result = bot.handle_extend_rest(data, "устал")
    assert "30 сек" in result  # дефолт


def test_handle_extend_rest_actually_extends_resting_until():
    data = w.load_workouts()
    with mock.patch("program.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)
        bot.handle_session_start(data)
    bot.handle_weight_answer(data, "121")
    bot.handle_set_confirmation(data)
    before = data["active_session"]["resting_until"]
    bot.handle_extend_rest(data, "продли на 30")
    after = data["active_session"]["resting_until"]
    assert after != before  # реально изменилось, не просто текст ответа
