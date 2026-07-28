"""Тесты для bot.py — сборка parser + workouts + progression + safety
через обработчики сообщений и callback-кнопок.

Реальная сеть (Telegram getUpdates/sendMessage, DeepSeek) не вызывается
ни в одном тесте — тестируем оркестрацию модулей, не сетевой слой (тот
уже покрыт test_parser.py через net.urlopen_retry).
"""
import os
import sys
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")

sys.path.insert(0, "..")
import bot
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
