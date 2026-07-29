#!/usr/bin/env python3
"""Проактивный таймер отдыха + напоминания — запускается GitHub Actions
workflow по Cron Trigger'у из cloudflare-worker/worker.js (см. cron_ping
в worker.js и .github/workflows/rest_timer.yml), не по прямому webhook
от Telegram.

Три независимые проверки за один прогон:
1. Таймер отдыха: session.rest_timer_expired() — истёк ли отдых
   текущего подхода И ещё не отправлено напоминание. Требует активной
   сессии.
2. Ежедневное напоминание: session.should_send_daily_reminder() —
   сегодня тренировочный день, время после 18:00 МСК (дефолт, точное
   время не выбрано), тренировки сегодня ещё не было. Требует
   ОТСУТСТВИЯ активной/завершённой сессии сегодня.
3. Напоминание о смене фазы периодизации: session.
   should_send_phase_reminder() — активный блок (силовой/объёмный/
   дефицитный) длится >= 6 недель (PHASE_REMINDER_WEEKS, середина
   диапазона 4-8, согласовано с Антоном 28.07.2026). Не зависит от
   активной сессии вообще — фаза живёт своей независимой временной
   шкалой.

Все три взаимоисключающи по большей части на практике, но проверяются
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


def build_phase_reminder_text(data):
    """Текст напоминания о том, что текущий блок периодизации длится
    уже >= 6 недель — пора решить, менять ли блок."""
    phase = w.get_active_phase(data)
    phase_info = prog.get_phase_info(phase["phase_id"])
    phase_name = phase_info["name"] if phase_info else phase["phase_id"]
    return (
        f"\U0001f504 Блок «{phase_name}» длится уже {sess.PHASE_REMINDER_WEEKS}+ недель.\n"
        f"Пора решить, менять ли фазу — напиши «фаза силовой», «фаза объёмный» "
        f"или «фаза дефицитный», если хочешь сменить, или просто продолжай."
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

    if sess.should_send_phase_reminder(data):
        tg_send(build_phase_reminder_text(data))
        w.mark_phase_reminder_sent(data)
        did_something = True
        print("Phase reminder sent.")

    if did_something:
        w.save_workouts(data)
    else:
        print("Nothing to do — no active rest timer, no daily reminder due, no phase reminder due.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
