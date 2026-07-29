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
    # Мокаем явно на день отдыха (вторник) — без этого тест хрупкий:
    # если реально запущен в тренировочный день после 18:00 МСК,
    # should_send_daily_reminder честно сработает, тест ложно упадёт
    data = w.load_workouts()
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)
    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = tuesday
        mock_prog_dt.now.return_value = tuesday
        result = timer.main()
    assert result == 0
    assert sent == []


def test_main_does_not_call_save_workouts_when_no_action():
    # Тихий no-op не должен трогать файл вовсе — экономит запись,
    # раз это подавляющее большинство прогонов Cron Trigger'а.
    # Тот же фикс — явный день отдыха, не полагаемся на реальное время.
    data = w.load_workouts()
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)
    with mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("timer.w.save_workouts") as mock_save, \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = tuesday
        mock_prog_dt.now.return_value = tuesday
        timer.main()
    mock_save.assert_not_called()


# --- ежедневное напоминание о тренировке -----------------------------

def test_build_daily_reminder_text_includes_day_name():
    with mock.patch("program.datetime", wraps=datetime) as mock_dt:
        mock_dt.now.return_value = datetime(2026, 7, 27, tzinfo=timezone.utc)  # понедельник
        text = timer.build_daily_reminder_text()
    assert "День 1" in text
    assert "Спина" in text
    assert "начал" in text


def test_main_sends_daily_reminder_when_due():
    data = w.load_workouts()
    data["sets"] = []
    late_monday = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)  # 19:00 МСК

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = late_monday
        mock_prog_dt.now.return_value = late_monday
        result = timer.main()

    assert result == 0
    assert len(sent) == 1
    assert "День 1" in sent[0]
    assert data["daily_reminder_sent_date"] == "2026-07-27"


def test_main_no_daily_reminder_on_rest_day():
    data = w.load_workouts()
    data["sets"] = []
    late_tuesday = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)  # 19:00 МСК, вторник

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = late_tuesday
        mock_prog_dt.now.return_value = late_tuesday
        timer.main()

    assert sent == []


def test_main_no_daily_reminder_if_already_trained():
    data = w.load_workouts()
    data["sets"] = [{"date": "2026-07-27", "exercise": "присед", "weight_kg": 50, "reps": 8, "set_number": 1}]
    late_monday = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = late_monday
        mock_prog_dt.now.return_value = late_monday
        timer.main()

    assert sent == []


def test_main_rest_timer_and_daily_reminder_are_independent():
    # Оба could в теории сработать в одном прогоне (не через elif) —
    # хотя на практике взаимоисключающи (rest timer требует активной
    # сессии, daily reminder требует её отсутствия сегодня), тест
    # фиксирует независимость проверок как контракт, не полагается на
    # эту взаимоисключаемость как гарантию
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    data["active_session"]["resting_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data):
        timer.main()

    # только rest reminder — есть активная сессия сегодня, значит
    # already_trained=True для daily reminder (проверка по data['sets'])
    assert len(sent) == 1
    assert "Отдых закончился" in sent[0]


# --- напоминание о смене фазы периодизации -----------------------------

def test_build_phase_reminder_text_includes_phase_name():
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-06-01")
    text = timer.build_phase_reminder_text(data)
    assert "Силовой" in text
    assert "6" in text  # PHASE_REMINDER_WEEKS
    assert "фаза" in text.lower()


def test_main_sends_phase_reminder_when_due():
    # Мокаем явно на день отдыха (вторник) — изолирует от
    # should_send_daily_reminder, который иначе тоже сработал бы
    # в реальный тренировочный день после 18:00 МСК
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-06-01")  # давно, больше 6 недель
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = tuesday
        mock_prog_dt.now.return_value = tuesday
        result = timer.main()

    assert result == 0
    assert len(sent) == 1
    assert "Силовой" in sent[0]
    assert data["active_phase"]["reminder_sent"] is True


def test_main_no_phase_reminder_too_early():
    data = w.load_workouts()
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)
    w.set_active_phase(data, "strength", tuesday.date().isoformat())  # только что

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = tuesday
        mock_prog_dt.now.return_value = tuesday
        timer.main()

    assert sent == []


def test_main_no_phase_reminder_when_default_volume():
    data = w.load_workouts()  # active_phase дефолтный, started_date=None
    tuesday = datetime(2026, 7, 28, tzinfo=timezone.utc)

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.load_workouts", return_value=data), \
         mock.patch("session.datetime", wraps=datetime) as mock_sess_dt, \
         mock.patch("program.datetime", wraps=datetime) as mock_prog_dt:
        mock_sess_dt.now.return_value = tuesday
        mock_prog_dt.now.return_value = tuesday
        timer.main()

    assert sent == []


def test_main_all_three_checks_independent():
    # Регрессия: все три проверки (rest timer, daily reminder, phase
    # reminder) должны работать в одном прогоне независимо, не
    # блокируя друг друга
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-06-01")
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    data["active_session"]["resting_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()

    sent = []
    with mock.patch("timer.tg_send", side_effect=lambda t: sent.append(t)), \
         mock.patch("timer.w.save_workouts"), \
         mock.patch("timer.w.load_workouts", return_value=data):
        timer.main()

    # rest timer сработал (истёк), phase reminder сработал (>6 недель) —
    # daily reminder НЕ сработал (already_trained=True, т.к. сессия
    # активна сегодня)
    assert len(sent) == 2
