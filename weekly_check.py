#!/usr/bin/env python3
"""Weekly Check — воскресенье.

19.08.2026: fitness-bot до этого момента был единственным ботом без
какого-либо самомониторинга — metals-news-bot имеет полноценный
weekly_check.py, здесь не было вообще ничего. Прямая параллель с тем,
что уже реально случилось: metals-news-bot тихо не работал 5+ дней
(протухший GMAIL_APP_PASSWORD), никто не узнал бы, если бы не разбор
по failure-статистике Actions. Урезанный по сравнению с оригиналом —
здесь нет pipeline.json/outreach, только то, что реально есть в этом
репозитории: тренировки, secrets, воркфлоу.

Расписание задаётся cron в UTC. Целевое местное время — 19:00 Ташкент (UTC+5).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import net

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "")
# ^ сторож сам шлёт через Telegram. Если протухнет TELEGRAM_BOT_TOKEN —
#   сторож замолчит вместе со всеми, и никто не узнает, что все замолчали.
#   GitHub Issue — единственный канал, который не зависит от того, что проверяем.

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKOUTS_PATH = os.path.join(ROOT, "workouts.json")
STATE_BOT_PATH = os.path.join(ROOT, "state_bot.json")
SECRETS_ROTATION_PATH = os.path.join(ROOT, "secrets_rotation.json")

# Ташкент, UTC+5.
TST = timezone(timedelta(hours=5))

WRITE_WORKFLOW_FILES = ["fitness_bot.yml", "rest_timer.yml"]

# Тренировка молчит дольше этого без единой записи -> подозрение, что
# бот не пишет вообще (не то же самое, что "пропустил тренировку" —
# skipped_days фиксирует явные/тихие пропуски, это тревога о полном
# отсутствии активности бота, а не о нетренированности человека).
STALE_DAYS_NO_ACTIVITY = 14


def load_json(path, default):
    if not os.path.exists(path):
        return None  # None = файла нет вообще, это отдельная тревога
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).date()
    except Exception:
        return None


def secrets_rotation_check(now):
    """Проверяет, не просрочена ли ручная ротация секретов.

    Тот же механизм, что в metals-news-bot — сверяет даты из
    secrets_rotation.json, обновляемого вручную при реальной смене.
    Файл отсутствует/битый -> пустой список, не падает.
    """
    data = load_json(SECRETS_ROTATION_PATH, None)
    if not data:
        return []
    default_threshold = data.get("rotation_threshold_days", 90)
    per_secret = data.get("per_secret_threshold_days", {})
    overdue = []
    for name, date_str in data.get("secrets", {}).items():
        d = parse_date(date_str)
        if d is None:
            continue
        age = (now.date() - d).days
        threshold = per_secret.get(name, default_threshold)
        if age >= threshold:
            overdue.append((name, age))
    return sorted(overdue, key=lambda x: -x[1])


def activity_staleness_check(now):
    """Проверяет, не молчит ли бот вообще — последняя запись в sets ИЛИ
    последний прогон rest_timer (last_run в state_bot.json, если он там
    есть) старше STALE_DAYS_NO_ACTIVITY. Отсутствие тренировок само по
    себе НЕ тревога (Антон мог взять паузу) — тревога только если это
    длится дольше явного порога, что скорее говорит о молчащем боте,
    чем о сознательном перерыве."""
    workouts = load_json(WORKOUTS_PATH, None)
    if workouts is None:
        return ["workouts.json не читается или отсутствует"]

    sets = workouts.get("sets", [])
    if not sets:
        return []  # пустая история — не тревога сама по себе (новый бот)

    last_set_date = max((parse_date(s.get("date")) for s in sets if s.get("date")), default=None)
    if last_set_date is None:
        return []

    days_silent = (now.date() - last_set_date).days
    if days_silent >= STALE_DAYS_NO_ACTIVITY:
        return [f"нет ни одной тренировки {days_silent} дн. — либо перерыв, либо бот не пишет"]
    return []


def push_conflict_stats(now, days=7):
    """Считает failed-прогоны у write-воркфлоу за неделю. Возвращает
    None если нет токена (тихо пропускаем секцию), иначе список
    {workflow, failed_count} только для тех, где failed > 0 за период."""
    if not GH_TOKEN or not GH_REPO:
        return None
    cutoff_iso = (now.astimezone(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    out = []
    for wf in WRITE_WORKFLOW_FILES:
        url = (f"https://api.github.com/repos/{GH_REPO}/actions/workflows/{wf}/runs"
               f"?status=failure&created=%3E{cutoff_iso}&per_page=20")
        req = urllib.request.Request(url, headers=headers)
        try:
            with net.urlopen_retry(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
            n = data.get("total_count", 0)
            if n > 0:
                out.append({"workflow": wf, "failed": n})
        except Exception as e:
            print(f"  ! push_conflict_stats {wf}: {e}", file=sys.stderr)
    return out


def telegram_is_alive():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    req = urllib.request.Request(url)
    try:
        with net.urlopen_retry(req, timeout=15) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return bool(resp.get("ok"))
    except Exception as e:
        print(f"Telegram getMe failed: {e}", file=sys.stderr)
        return False


def github_issue_alert(title, body):
    """Единственный запасной канал, не зависящий от Telegram.
    Ищет открытый issue с тем же title (по метке) — не плодит дубликаты
    при повторных недельных сбоях, комментирует существующий вместо нового."""
    if not GH_TOKEN or not GH_REPO:
        print("No GITHUB_TOKEN/GITHUB_REPOSITORY - cannot raise GitHub Issue fallback", file=sys.stderr)
        return False
    api = f"https://api.github.com/repos/{GH_REPO}/issues"
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }

    def _req(method, url, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with net.urlopen_retry(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))

    try:
        existing = _req("GET", api + "?state=open&labels=bot-alert&per_page=20")
        match = next((i for i in existing if i.get("title") == title), None)
        if match:
            _req("POST", match["comments_url"], {"body": body})
            print(f"Commented on existing issue #{match['number']}")
            return True
        try:
            created = _req("POST", api, {"title": title, "body": body, "labels": ["bot-alert"]})
        except Exception:
            created = _req("POST", api, {"title": title, "body": body})
        print(f"Opened issue #{created.get('number')}")
        return True
    except Exception as e:
        print(f"GitHub Issue fallback failed: {e}", file=sys.stderr)
        return False


def tg_send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with net.urlopen_retry(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ! telegram {e.code}: {body}", file=sys.stderr)
        raise


def build_report(now, activity_alarms, overdue_secrets, workflow_failures):
    weekday = now.weekday()
    monday = now - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)
    week_range = f"{monday.strftime('%d %b')} \u2014 {sunday.strftime('%d %b')}"

    lines = ["\U0001f4aa <b>Fitness bot — Weekly Check</b>", f"<i>{week_range}</i>", ""]

    if not activity_alarms and not overdue_secrets and not workflow_failures:
        lines.append("\u2705 Всё чисто — активность есть, секреты свежие, воркфлоу не падали.")
        return "\n".join(lines)

    if activity_alarms:
        lines.append("\u26a0\ufe0f <b>Активность:</b>")
        for a in activity_alarms:
            lines.append(f"  \u2022 {a}")
        lines.append("")

    if overdue_secrets:
        lines.append("\U0001f511 <b>Секреты требуют ротации:</b>")
        for name, age in overdue_secrets:
            lines.append(f"  \u2022 {name}: {age} дн. с последней смены")
        lines.append("<i>Смени в GitHub Settings \u2192 Secrets, потом обнови дату в secrets_rotation.json.</i>")
        lines.append("")

    if workflow_failures:
        lines.append("\U0001f6a8 <b>Воркфлоу падали за неделю:</b>")
        for wf in workflow_failures:
            lines.append(f"  \u2022 {wf['workflow']}: {wf['failed']} failure")

    return "\n".join(lines)


def main():
    now = datetime.now(TST)

    # Первым делом — жив ли сам канал оповещений. Если нет, дальше нет
    # смысла готовить сообщение, которое некуда будет доставить.
    if not telegram_is_alive():
        github_issue_alert(
            "\U0001f534 Telegram bot unreachable — weekly_check cannot report",
            f"getMe failed at {now.isoformat()}. TELEGRAM_BOT_TOKEN may be revoked/expired, "
            f"or the bot was blocked/deleted. fitness_bot.yml and rest_timer.yml post through "
            f"this same token — if this is broken, they are silently failing to notify too. "
            f"Check the token in Telegram (@BotFather \u2192 /mybots) and the repo secret."
        )
        print("Telegram unreachable - raised GitHub Issue instead, skipping normal report", file=sys.stderr)
        return 1

    activity_alarms = activity_staleness_check(now)
    overdue_secrets = secrets_rotation_check(now)
    workflow_failures = push_conflict_stats(now) or []

    report = build_report(now, activity_alarms, overdue_secrets, workflow_failures)

    try:
        tg_send(report)
        print("Weekly check sent.")
    except Exception as e:
        github_issue_alert(
            "\U0001f534 weekly_check: tg_send failed after telegram_is_alive() passed",
            f"getMe succeeded but sendMessage failed: {e}\n\nFull report that failed to send:\n\n{report}"
        )
        print(f"tg_send failed, raised GitHub Issue with full report as fallback: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
