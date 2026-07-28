#!/usr/bin/env python3
"""Fitness bot — главный обработчик.

Поток:
1. getUpdates (с offset, как filings.py) — забирает и текстовые
   сообщения, и callback_query (нажатия кнопок).
2. Текстовое сообщение -> parser.parse_workout_text():
   - uncertain=true -> переспрашиваем конкретным вопросом, НЕ пишем
     в workouts.json (неверная запись веса портит историю прогрессии
     на много тренировок вперёд, дешевле переспросить).
   - uncertain=false -> для каждого сета: safety.check_exercise()
     (сохраняется в записи, не блокирует запись факта — Антон мог
     реально сделать запрещённое упражнение, это его тело и его
     выбор; блокируется только АВТОМАТИЧЕСКОЕ ПРЕДЛОЖЕНИЕ прогрессии,
     не сама запись истории) -> workouts.add_set() -> после записи
     всех сетов сессии: progression.suggest_progression() -> если
     есть предложение, отправляем с кнопками 👍/👎.
3. callback_query (подтверждение/отклонение) -> confirmed:
   workouts.set_target() с предложенными весом/повторами, rejected:
   просто помечаем pending_suggestion как rejected, target не меняем.

Состояние диалога ("бот ждёт уточнения от Антона") НЕ хранится между
запусками бота — каждый прогон обрабатывает все новые апдейты с нуля.
Если Антон не ответил на уточняющий вопрос до следующего прогона —
вопрос просто останется висеть в чате, следующее сообщение будет
разобрано заново с нуля (может быть, ответом на старый вопрос, может
быть, новой записью — parser.py не знает контекста предыдущего вопроса
в этой версии, это следующий шаг развития, не в первой версии).
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import net
import parser
import progression
import safety
import workouts as w

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, "state_bot.json")


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"tg_offset": 0}


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg_get_updates(offset):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"timeout": 0, "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset:
        params["offset"] = offset
    try:
        with net.urlopen_retry(url + "?" + urllib.parse.urlencode(params), timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp.get("result", [])
    except Exception as e:
        print(f"  ! getUpdates error: {e}", file=sys.stderr)
        return []


def tg_send(text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with net.urlopen_retry(req, timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return (resp.get("result") or {}).get("message_id")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ! telegram error {e.code}: {body}", file=sys.stderr)
        raise


def tg_answer_callback(callback_id, text=""):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_id, "text": text}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with net.urlopen_retry(req, timeout=20) as r:
            r.read()
    except Exception as e:
        print(f"  ! answerCallbackQuery error: {e}", file=sys.stderr)


def suggestion_keyboard(suggestion_id):
    return {
        "inline_keyboard": [[
            {"text": "\U0001f44d согласен", "callback_data": f"sugg:confirm:{suggestion_id}"},
            {"text": "\U0001f44e не сейчас", "callback_data": f"sugg:reject:{suggestion_id}"},
        ]]
    }


def make_suggestion_id(exercise):
    raw = f"{exercise}|{datetime.now(timezone.utc).isoformat()}"
    return "sugg_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def handle_workout_message(text, data):
    """Парсит текст, записывает сеты, возвращает список текстов для
    отправки (подтверждение записи + возможное предложение прогрессии),
    либо один текст с уточняющим вопросом при uncertain."""
    parsed = parser.parse_workout_text(text)

    if parsed.get("uncertain"):
        return [parsed["question"]]

    sets = parsed.get("sets", [])
    if not sets:
        return ["Не нашёл в сообщении ни одного подхода — можешь переформулировать?"]

    today = datetime.now(timezone.utc).date().isoformat()
    recorded_exercises = set()
    confirmation_lines = ["\u2705 <b>Записал:</b>"]

    exercise_set_counters = {}
    for s in sets:
        raw_name = s.get("exercise", "")
        exercise_set_counters[raw_name] = exercise_set_counters.get(raw_name, 0) + 1
        set_number = exercise_set_counters[raw_name]

        normalized_preview = w.normalize_exercise_name(raw_name, data.get("exercise_aliases", {}))
        safety_result = safety.check_exercise(normalized_preview)

        entry = w.add_set(
            data, raw_name, today,
            s.get("weight_kg"), s.get("reps"), set_number,
            rpe=s.get("rpe"), note=s.get("note", ""),
            safety_status=safety_result["status"],
        )
        recorded_exercises.add(entry["exercise"])

        weight_str = f"{entry['weight_kg']}кг \u00d7 " if entry["weight_kg"] else ""
        confirmation_lines.append(f"\u2022 {esc(entry['exercise'])}: {weight_str}{entry['reps']}")

    messages = ["\n".join(confirmation_lines)]

    for exercise in recorded_exercises:
        suggestion = progression.suggest_progression(data, exercise)
        if suggestion:
            suggestion_id = make_suggestion_id(exercise)
            data.setdefault("pending_suggestions", []).append({
                "id": suggestion_id,
                "exercise": exercise,
                "suggested_weight_kg": suggestion["suggested_weight_kg"],
                "suggested_reps": suggestion["suggested_reps"],
                "reasoning": suggestion["reasoning"],
                "message_id": None,
                "status": "pending",
                "created_ts": datetime.now(timezone.utc).isoformat(),
            })
            messages.append((suggestion_id, progression.format_suggestion_message(exercise, suggestion)))

    return messages


def handle_callback(callback_data, data):
    """Обрабатывает нажатие кнопки подтверждения/отклонения предложения.
    Возвращает текст для answerCallbackQuery (короткое всплывающее
    уведомление) или None, если callback_data не наш формат."""
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] != "sugg":
        return None
    action, suggestion_id = parts[1], parts[2]

    suggestions = data.get("pending_suggestions", [])
    match = next((s for s in suggestions if s["id"] == suggestion_id), None)
    if not match:
        return "Предложение уже неактуально"

    if match["status"] != "pending":
        return "Уже обработано"

    if action == "confirm":
        w.set_target(data, match["exercise"], match["suggested_weight_kg"], match["suggested_reps"])
        match["status"] = "confirmed"
        return f"Принято: {match['exercise']} {match['suggested_weight_kg']}кг"
    elif action == "reject":
        match["status"] = "rejected"
        return "Понял, оставляю как есть"
    return None


def main():
    state = load_state()
    data = w.load_workouts()

    offset = state.get("tg_offset") or 0
    updates = tg_get_updates(offset + 1 if offset else None)

    outgoing = []  # список (text, reply_markup_or_None) для отправки после обработки всех апдейтов

    for u in updates:
        state["tg_offset"] = max(state.get("tg_offset") or 0, u.get("update_id", 0))

        cb = u.get("callback_query")
        if cb:
            result_text = handle_callback(cb.get("data", ""), data)
            tg_answer_callback(cb.get("id", ""), result_text or "")
            continue

        msg = u.get("message") or {}
        text = msg.get("text", "")
        if not text or text.startswith("/start"):
            continue

        results = handle_workout_message(text, data)
        for item in results:
            if isinstance(item, tuple):
                suggestion_id, msg_text = item
                outgoing.append((msg_text, suggestion_keyboard(suggestion_id), suggestion_id))
            else:
                outgoing.append((item, None, None))

    # Сохраняем ДО отправки в Telegram — тот же принцип, что в
    # pipeline_sync.py: если Telegram упадёт при отправке, факт записи
    # подхода и pending_suggestions уже не потеряются.
    w.save_workouts(data)
    save_state(state)

    for item in outgoing:
        text, markup, suggestion_id = item
        mid = tg_send(text, reply_markup=markup)
        if suggestion_id and mid:
            # message_id не критичен для работы (подтверждение идёт по
            # suggestion_id в callback_data, не по message_id) — просто
            # полезен для отладки, чтобы найти сообщение в чате руками
            for s in data.get("pending_suggestions", []):
                if s["id"] == suggestion_id:
                    s["message_id"] = mid
                    break

    if any(item[2] for item in outgoing):
        w.save_workouts(data)  # пересохранить только ради необязательного message_id

    print(f"Processed {len(updates)} update(s), sent {len(outgoing)} message(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
