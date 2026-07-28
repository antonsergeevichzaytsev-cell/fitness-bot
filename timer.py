#!/usr/bin/env python3
"""Проактивный таймер отдыха — запускается GitHub Actions workflow по
Cron Trigger'у из cloudflare-worker/worker.js (см. cron_ping в worker.js
и .github/workflows/rest_timer.yml), не по прямому webhook от Telegram.

Логика: session.rest_timer_expired() проверяет, истёк ли отдых текущего
подхода И ещё не отправлено напоминание (reminder_sent=False). Если да —
отправляет сообщение в Telegram, помечает reminder_sent=True.

Если сессия неактивна или таймер не истёк — тихо завершается без
действий (это ожидаемый исход в большинстве прогонов: Cron стучится
каждую минуту, но реальный отдых истекает раз в 45-90 секунд между
подходами, и совсем не истекает вне активной тренировки)."""
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import net
import program as prog
import session as sess
import workouts as w

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def tg_send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with net.urlopen_retry(req, timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ! telegram error {e.code}: {body}", file=sys.stderr)
        raise


def build_reminder_text(data):
    """Текст напоминания — какой подход/упражнение сейчас, из плана."""
    ex, set_num = sess.current_exercise_info(data)
    if ex is None:
        return None
    return (
        f"\u23f0 Отдых закончился — пора продолжать!\n\n"
        f"{prog.format_exercise_line(ex)}\n"
        f"Подход {set_num}/{ex['sets']}. Напиши «взял», когда сделаешь."
    )


def main():
    data = w.load_workouts()

    if not sess.rest_timer_expired(data):
        print("Rest timer not expired or no active session — nothing to do.")
        return 0

    text = build_reminder_text(data)
    if text is None:
        print("Timer expired but no current exercise — inconsistent state, skipping send.", file=sys.stderr)
        return 0

    tg_send(text)
    sess.mark_reminder_sent(data)
    w.save_workouts(data)
    print("Reminder sent, reminder_sent marked True.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
