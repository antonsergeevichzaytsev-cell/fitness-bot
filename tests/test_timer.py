"""Тесты для timer.py — проактивный таймер отдыха, вызывается Cron
Trigger'ом раз в минуту.

Реальная сеть не вызывается — мокируем tg_send и net.urlopen_retry,
как в test_bot.py/test_parser.py.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")

sys.path.insert(0, "..")
import session as sess
import timer
import workouts as w


def _session_with_expired_timer():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    data["active_session"]["resting_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()
    return data


# --- build_reminder_text --------------------------------------------

def test_build_reminder_text_includes_exercise_and_set():
    data = _session_with_expired_timer()
    text = timer.build_reminder_text(data)
    assert "Vertical Traction" in text
    assert "Подход 2/4" in text
    assert "взял" in text


def test_build_reminder_text_none_when_no_current_exercise():
    data = w.load_workouts()  # нет активной сессии вообще
    assert timer.build_reminder_text(data) is None


# --- main(): истёкший таймер -> отправка ------------------------------

def test_main_sends_reminder_when_timer_expired():
    data = _session_with_expired_timer()
    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data):
        result = timer.main()
    assert result == 0
    assert len(sent) == 1
    assert data["active_session"]["reminder_sent"] is True


def test_main_does_not_resend_after_marked():
    data = _session_with_expired_timer()
    sess.mark_reminder_sent(data)  # уже отправлено ранее
    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data):
        timer.main()
    assert sent == []


# --- main(): таймер не истёк -> тихий no-op ----------------------------

def test_main_no_action_when_timer_not_expired():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)  # только что записан
    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data):
        result = timer.main()
    assert result == 0
    assert sent == []


def test_main_no_action_without_active_session():
    data = w.load_workouts()
    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data):
        result = timer.main()
    assert result == 0
    assert sent == []


def test_main_does_not_call_save_workouts_when_no_action():
    # Тихий no-op не должен трогать файл вовсе — экономит запись,
    # раз это подавляющее большинство прогонов Cron Trigger'а
    data = w.load_workouts()
    with mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("timer.w.save_workouts") as mock_save:
        timer.main()
    mock_save.assert_not_called()
