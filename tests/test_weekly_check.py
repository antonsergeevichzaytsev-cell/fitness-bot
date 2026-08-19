"""Тесты для weekly_check.py — сторож fitness-bot, добавлен 19.08.2026.

До этого момента fitness-bot был единственным ботом без самомониторинга
вообще (в отличие от metals-news-bot, где weekly_check.py уже был).
Этот файл — урезанная версия того же паттерна: не pipeline/outreach,
а активность тренировок + secrets rotation + failure-статистика
воркфлоу + telegram-жив-проверка с GitHub Issue fallback.

weekly_check.py на верхнем уровне читает os.environ.
"""
import os
import sys
from datetime import datetime, timedelta
from unittest import mock

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("TELEGRAM_CHAT_ID", "test")
os.environ.setdefault("GITHUB_TOKEN", "test")
os.environ.setdefault("GITHUB_REPOSITORY", "test/test")

sys.path.insert(0, "..")
import weekly_check as wc

TST = wc.TST


# --- parse_date ------------------------------------------------------------

def test_parse_date_valid():
    d = wc.parse_date("2026-07-27")
    assert d.year == 2026 and d.month == 7 and d.day == 27


def test_parse_date_none_for_empty():
    assert wc.parse_date(None) is None
    assert wc.parse_date("") is None


def test_parse_date_none_for_malformed():
    assert wc.parse_date("not-a-date") is None


# --- activity_staleness_check -----------------------------------------------

def test_activity_no_alarm_when_recent_set_exists():
    now = datetime.now(TST)
    recent = (now.date() - timedelta(days=3)).isoformat()
    workouts = {"sets": [{"date": recent}]}
    with mock.patch("weekly_check.load_json", return_value=workouts):
        alarms = wc.activity_staleness_check(now)
    assert alarms == []


def test_activity_alarm_when_stale():
    now = datetime.now(TST)
    old = (now.date() - timedelta(days=20)).isoformat()
    workouts = {"sets": [{"date": old}]}
    with mock.patch("weekly_check.load_json", return_value=workouts):
        alarms = wc.activity_staleness_check(now)
    assert len(alarms) == 1
    assert "20 дн" in alarms[0]


def test_activity_no_alarm_exactly_below_threshold():
    now = datetime.now(TST)
    just_under = (now.date() - timedelta(days=wc.STALE_DAYS_NO_ACTIVITY - 1)).isoformat()
    workouts = {"sets": [{"date": just_under}]}
    with mock.patch("weekly_check.load_json", return_value=workouts):
        alarms = wc.activity_staleness_check(now)
    assert alarms == []


def test_activity_alarm_at_exact_threshold():
    now = datetime.now(TST)
    at_threshold = (now.date() - timedelta(days=wc.STALE_DAYS_NO_ACTIVITY)).isoformat()
    workouts = {"sets": [{"date": at_threshold}]}
    with mock.patch("weekly_check.load_json", return_value=workouts):
        alarms = wc.activity_staleness_check(now)
    assert len(alarms) == 1


def test_activity_no_alarm_when_no_sets_at_all():
    """Пустая история (новый бот, ещё не начал тренироваться) — не
    тревога сама по себе, отличается от 'молчит N дней' после того,
    как активность уже была."""
    now = datetime.now(TST)
    with mock.patch("weekly_check.load_json", return_value={"sets": []}):
        alarms = wc.activity_staleness_check(now)
    assert alarms == []


def test_activity_alarm_when_workouts_missing():
    now = datetime.now(TST)
    with mock.patch("weekly_check.load_json", return_value=None):
        alarms = wc.activity_staleness_check(now)
    assert len(alarms) == 1
    assert "отсутствует" in alarms[0] or "не читается" in alarms[0]


def test_activity_uses_most_recent_set_not_first():
    now = datetime.now(TST)
    old = (now.date() - timedelta(days=30)).isoformat()
    recent = (now.date() - timedelta(days=2)).isoformat()
    workouts = {"sets": [{"date": old}, {"date": recent}]}
    with mock.patch("weekly_check.load_json", return_value=workouts):
        alarms = wc.activity_staleness_check(now)
    assert alarms == []  # max(old, recent) = recent -> не просрочено


# --- secrets_rotation_check (та же логика, что в metals-news-bot) ----------

def test_secrets_rotation_no_overdue_when_all_fresh():
    now = datetime.now(TST)
    fresh = (now.date() - timedelta(days=10)).strftime("%Y-%m-%d")
    data = {"secrets": {"TELEGRAM_BOT_TOKEN": fresh}, "rotation_threshold_days": 90}
    with mock.patch("weekly_check.load_json", return_value=data):
        overdue = wc.secrets_rotation_check(now)
    assert overdue == []


def test_secrets_rotation_flags_overdue_secret():
    now = datetime.now(TST)
    old = (now.date() - timedelta(days=95)).strftime("%Y-%m-%d")
    data = {"secrets": {"TELEGRAM_BOT_TOKEN": old}, "rotation_threshold_days": 90}
    with mock.patch("weekly_check.load_json", return_value=data):
        overdue = wc.secrets_rotation_check(now)
    assert overdue == [("TELEGRAM_BOT_TOKEN", 95)]


def test_secrets_rotation_missing_file_returns_empty_not_crash():
    now = datetime.now(TST)
    with mock.patch("weekly_check.load_json", return_value=None):
        overdue = wc.secrets_rotation_check(now)
    assert overdue == []


def test_secrets_rotation_per_secret_threshold_overrides_default():
    now = datetime.now(TST)
    age_50 = (now.date() - timedelta(days=50)).strftime("%Y-%m-%d")
    data = {
        "secrets": {"TELEGRAM_BOT_TOKEN": age_50},
        "rotation_threshold_days": 90,
        "per_secret_threshold_days": {"TELEGRAM_BOT_TOKEN": 45},
    }
    with mock.patch("weekly_check.load_json", return_value=data):
        overdue = wc.secrets_rotation_check(now)
    assert overdue == [("TELEGRAM_BOT_TOKEN", 50)]


# --- telegram_is_alive / github_issue_alert ---------------------------------

def test_telegram_is_alive_true_on_ok_response():
    class FakeResp:
        def read(self):
            return b'{"ok": true}'
        def __enter__(self): return self
        def __exit__(self, *a): return False
    with mock.patch("weekly_check.net.urlopen_retry", return_value=FakeResp()):
        assert wc.telegram_is_alive() is True


def test_telegram_is_alive_false_on_exception():
    with mock.patch("weekly_check.net.urlopen_retry", side_effect=OSError("timeout")):
        assert wc.telegram_is_alive() is False


def test_github_issue_alert_no_token_returns_false():
    with mock.patch.object(wc, "GH_TOKEN", ""):
        assert wc.github_issue_alert("title", "body") is False


# --- main(): telegram unreachable -> GitHub Issue fallback, no crash -------
# 19.08.2026: если сам канал алертов (Telegram) недоступен, main() не
# должна пытаться слать туда отчёт — вместо этого эскалирует через
# GitHub Issue и завершается без исключения (return 1, не raise).

def test_main_raises_github_issue_when_telegram_unreachable():
    with mock.patch.object(wc, "telegram_is_alive", return_value=False):
        with mock.patch.object(wc, "github_issue_alert", return_value=True) as mock_issue:
            result = wc.main()
    assert result == 1
    mock_issue.assert_called_once()
    title = mock_issue.call_args[0][0]
    assert "Telegram" in title or "unreachable" in title.lower()


def test_main_sends_report_when_telegram_alive():
    with mock.patch.object(wc, "telegram_is_alive", return_value=True):
        with mock.patch.object(wc, "activity_staleness_check", return_value=[]):
            with mock.patch.object(wc, "secrets_rotation_check", return_value=[]):
                with mock.patch.object(wc, "push_conflict_stats", return_value=[]):
                    with mock.patch.object(wc, "tg_send") as mock_send:
                        result = wc.main()
    assert result == 0
    mock_send.assert_called_once()
    assert "Всё чисто" in mock_send.call_args[0][0]


# --- build_report ------------------------------------------------------------

def test_build_report_all_clean():
    now = datetime.now(TST)
    text = wc.build_report(now, [], [], [])
    assert "Всё чисто" in text


def test_build_report_includes_all_sections_when_present():
    now = datetime.now(TST)
    text = wc.build_report(
        now,
        ["нет ни одной тренировки 20 дн. — либо перерыв, либо бот не пишет"],
        [("TELEGRAM_BOT_TOKEN", 95)],
        [{"workflow": "rest_timer.yml", "failed": 3}],
    )
    assert "20 дн" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "95 дн" in text
    assert "rest_timer.yml" in text
    assert "3 failure" in text
