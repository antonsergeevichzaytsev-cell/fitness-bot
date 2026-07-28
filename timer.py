#!/usr/bin/env python3
"""Проактивный таймер отдыха + напоминание о тренировке — запускается
GitHub Actions workflow по Cron Trigger'у из cloudflare-worker/worker.js
(см. cron_ping в worker.js и .github/workflows/rest_timer.yml), не по
прямому webhook от Telegram.

Две независимые проверки за один прогон:
1. Таймер отдыха: session.rest_timer_expired() — истёк ли отдых
   текущего подхода И ещё не отправлено напоминание. Требует активной
   сессии.
2. Ежедневное напоминание: session.should_send_daily_reminder() —
   сегодня тренировочный день, время после 18:00 МСК (дефолт, точное
   время не выбрано), тренировки сегодня ещё не было. Требует
   ОТСУТСТВИЯ активной/завершённой сессии сегодня.

Оба взаимоисключающи на практике (первая требует активной сессии,
вторая — что тренировки сегодня не было вообще), но проверяются
независимо, не через elif — на случай будущих изменений условий, где
это перестанет быть строго взаимоисключающим.

Если ни одна проверка не сработала — тихо завершается без действий
(ожидаемый исход в большинстве прогонов: Cron стучится каждую минуту,
реальные события реже)."""
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
    """Текст напоминания об истёкшем отдыхе — какой подход/упражнение
    сейчас, из плана."""
    ex, set_num = sess.current_exercise_info(data)
    if ex is None:
        return None
    return (
        f"\u23f0 Отдых закончился — пора продолжать!\n\n"
        f"{prog.format_exercise_line(ex)}\n"
        f"Подход {set_num}/{ex['sets']}. Напиши «взял», когда сделаешь."
    )


def build_daily_reminder_text():
    """Текст ежедневного напоминания о тренировке — какой день по
    расписанию сегодня."""
    day_id = prog.today_day_id()
    day = prog.get_day_plan(day_id) if day_id else None
    day_name = day["name"] if day else "тренировка"
    return (
        f"\U0001f4aa Сегодня по расписанию: День {day_id} — {day_name}.\n"
        f"Ещё не начал? Напиши «начал», когда будешь готов."
    )


def main():
    data = w.load_workouts()
    did_something = False

    if sess.rest_timer_expired(data):
        text = build_reminder_text(data)
        if text is None:
            print("Timer expired but no current exercise — inconsistent state, skipping send.", file=sys.stderr)
        else:
            tg_send(text)
            sess.mark_reminder_sent(data)
            did_something = True
            print("Rest reminder sent.")

    if sess.should_send_daily_reminder(data):
        tg_send(build_daily_reminder_text())
        sess.mark_daily_reminder_sent(data)
        did_something = True
        print("Daily workout reminder sent.")

    if did_something:
        w.save_workouts(data)
    else:
        print("Nothing to do — no active rest timer, no daily reminder due.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
