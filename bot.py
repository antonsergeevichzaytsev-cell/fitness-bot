#!/usr/bin/env python3
"""Fitness bot — главный обработчик.

Запускается по Telegram webhook через Cloudflare Worker + GitHub
repository_dispatch (см. cloudflare-worker/worker.js).

28.07.2026 КРИТИЧНЫЙ ФИКС: изначально апдейт читался через getUpdates
после того, как Worker дёргал workflow_dispatch. Не работает — Telegram
API не даёт getUpdates и активному webhook работать одновременно
(апдейты уходят либо туда, либо туда). Первое реальное сообщение
прошло всю цепочку успешно (Worker -> GitHub Actions), но bot.py внутри
не получил его — бот молчал. Исправлено: Worker теперь передаёт САМО
ТЕЛО Telegram update через repository_dispatch client_payload, GitHub
Actions кладёт его в переменную TELEGRAM_UPDATE_JSON — bot.py читает
оттуда напрямую, не делает сетевой запрос к Telegram вообще.

Поток:
0. Session gate (session.py, ДЕТЕРМИНИРОВАННО, не через DeepSeek):
   "начал"/"погнали" -> открывает сессию. "закончил"/"финиш" -> строит
   отчёт по всем упражнениям сессии с трендами (тоннаж vs прошлая
   тренировка) и закрывает сессию. И то и другое — короткий путь,
   не доходит до парсинга через DeepSeek.
1. Апдейт читается из TELEGRAM_UPDATE_JSON (один Update объект —
   message или callback_query, ровно тот, что Telegram прислал на
   webhook). workflow_dispatch без payload -> No-op, не падает.
2. Текстовое сообщение (не начало/конец сессии) -> parser.parse_workout_text():
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
import program as prog
import progression
import safety
import sanity
import session as sess
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
    """НЕ ИСПОЛЬЗУЕТСЯ с 28.07.2026 (см. фикс в docstring модуля) —
    getUpdates не работает, пока активен webhook. Оставлена в коде
    как задокументированный факт "это не работает так, как кажется
    интуитивно", не как рабочий путь. Если когда-нибудь понадобится
    вернуться к polling — сначала deleteWebhook, иначе Telegram
    вернёт 409 Conflict."""
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
    либо один текст с уточняющим вопросом при uncertain ИЛИ подозрительном
    значении (sanity.check_weight_reps_sanity)."""
    parsed = parser.parse_workout_text(text)

    if parsed.get("uncertain"):
        return [parsed["question"]]

    sets = parsed.get("sets", [])
    if not sets:
        return ["Не нашёл в сообщении ни одного подхода — можешь переформулировать?"]

    # Проверка реалистичности ПЕРЕД записью любого сета — если хоть один
    # подозрителен (вероятная опечатка), останавливаемся и переспрашиваем,
    # НЕ записываем НИ ОДИН сет из этого сообщения. Частичная запись
    # ("записал первые 2 подхода, а 3-й спросил") сложнее для Антона
    # понять, что уже в истории, а что нет — проще переспросить всё сразу.
    for s in sets:
        sanity_result = sanity.check_weight_reps_sanity(
            data, s.get("exercise", ""), s.get("weight_kg"), s.get("reps"),
            aliases=data.get("exercise_aliases", {}),
        )
        if sanity_result["suspicious"]:
            return [sanity_result["question"]]

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


def handle_session_start(data):
    """Обрабатывает 'начал' — определяет день программы по расписанию
    (program.today_day_id, детерминированно, не DeepSeek), открывает
    сессию с этим днём. Сначала спрашивает вес тела (нужен для расчёта
    массы/тоннажа и калорий за тренировку) — план дня показывается
    только ПОСЛЕ ответа на этот вопрос, через handle_weight_answer().

    Если сегодня день отдыха по расписанию — сессия НЕ открывается с
    day_id (программы нет, current_exercise_order будет None, весь
    пошаговый флоу не сработает бы) — вместо этого честно сообщаем,
    что сегодня отдых, не притворяемся, что есть план."""
    day_id = prog.today_day_id()

    if day_id is None:
        return "Сегодня по расписанию день отдыха. Если всё равно хочешь потренироваться — напиши мне подходы обычным текстом, я запишу без плана."

    started = sess.start_session(data, day_id=day_id)
    if not started:
        if sess.is_awaiting_weight_input(data):
            return "Жду твой вес до тренировки — напиши число в кг."
        ex, set_num = sess.current_exercise_info(data)
        if ex:
            return f"Тренировка уже идёт. Сейчас: {prog.format_exercise_line(ex)}\nПодход {set_num}/{ex['sets']}."
        return "Тренировка уже идёт — просто пиши подходы."

    return "\U0001f4aa Начинаем! Сколько сейчас весишь (кг)? Нужно для расчёта тоннажа и калорий за тренировку."


def handle_weight_answer(data, text):
    """Обрабатывает ответ на вопрос о весе тела — session.parse_weight_kg
    вытаскивает число, session.set_body_weight сохраняет, снимает флаг
    ожидания веса и ставит флаг ожидания дневника самочувствия (сон/
    стресс — следующий вопрос перед показом плана)."""
    weight = sess.parse_weight_kg(text)
    if weight is None:
        return "Не понял вес — напиши просто число, например 121 или 121.5."

    sess.set_body_weight(data, weight)
    return "Как спал и какой стресс? Например «спал 7, стресс 4» — или просто «нормально», если не хочешь цифрами."


def handle_wellness_answer(data, text):
    """Обрабатывает ответ на вопрос о сне/стрессе — session.
    parse_wellness_answer гибко извлекает числа (если есть),
    session.set_wellness сохраняет. После этого показывает план дня +
    первое упражнение (то, что раньше показывал handle_weight_answer
    сразу после веса)."""
    parsed = sess.parse_wellness_answer(text)
    sess.set_wellness(data, parsed["sleep_hours"], parsed["stress_level"], parsed["raw_note"])

    session = data["active_session"]
    day_id = session["day_id"]
    day_plan = prog.format_day_plan_with_targets(day_id, data)
    ex, set_num = sess.current_exercise_info(data)
    first_exercise = prog.format_exercise_line(ex)
    return (
        f"{day_plan}\n\n"
        f"\U0001f4aa Начинаем с упражнения 1:\n{first_exercise}\n\n"
        f"Сделай подход {set_num}/{ex['sets']} и напиши «взял»."
    )


def handle_replace_request(data, reason_text):
    """Обрабатывает просьбу заменить текущее упражнение — вызывает
    parser.suggest_replacement (DeepSeek), затем ОБЯЗАТЕЛЬНО проверяет
    результат через safety.check_exercise ПРЕЖДЕ чем показать
    пользователю (промпт уже называет стоп-лист явно, но код-проверка
    здесь — та же архитектура, что и в safety.py: промпт может быть
    проигнорирован моделью, код-проверка не может).

    Возвращает (message_text, reply_markup_or_None). При safety-блоке
    предложение НЕ показывается вообще — вместо него честное сообщение,
    что предложенная замена задета запретом, попробовать другую
    формулировку причины."""
    ex, set_num = sess.current_exercise_info(data)
    if ex is None:
        return "Сейчас нет активного упражнения для замены — напиши «начал», чтобы начать тренировку.", None

    suggestion = parser.suggest_replacement(ex, reason=reason_text)
    if suggestion.get("error"):
        return suggestion.get("question", "Не удалось предложить замену."), None

    safety_result = safety.check_exercise(suggestion["replacement_name"])
    if safety_result["status"] == "hard_block":
        print(f"  ! DeepSeek suggested banned replacement: {suggestion['replacement_name']} ({safety_result['pattern']})",
              file=sys.stderr)
        return (
            "Не могу предложить замену — модель предложила запрещённое упражнение "
            "(стоп-лист программы). Попробуй уточнить причину замены другими словами."
        ), None

    replacement_ex = {
        "name": suggestion["replacement_name"],
        "machine": suggestion.get("machine", ""),
        "sets": ex["sets"],  # число подходов не меняем — только само упражнение
        "reps_min": suggestion.get("reps_min", ex["reps_min"]),
        "reps_max": suggestion.get("reps_max", ex["reps_max"]),
        "weight_min_kg": suggestion.get("weight_min_kg"),
        "weight_max_kg": suggestion.get("weight_max_kg"),
        "tempo": suggestion.get("tempo", ex["tempo"]),
        "rest_sec": suggestion.get("rest_sec", ex["rest_sec"]),
        "order": ex["order"],
        "per_side": ex.get("per_side", False),
    }

    suggestion_id = make_suggestion_id(f"repl_{ex['order']}")
    data.setdefault("pending_suggestions", []).append({
        "id": suggestion_id,
        "type": "replacement",
        "order": ex["order"],
        "replacement": replacement_ex,
        "status": "pending",
        "created_ts": datetime.now(timezone.utc).isoformat(),
    })

    text = (
        f"\U0001f504 Замена для «{ex['name']}»:\n\n"
        f"{prog.format_exercise_line(replacement_ex)}\n\n"
        f"{suggestion.get('reasoning', '')}"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "\U0001f44d заменить", "callback_data": f"repl:confirm:{suggestion_id}"},
            {"text": "\U0001f44e не то", "callback_data": f"repl:reject:{suggestion_id}"},
        ]]
    }
    return text, markup


def handle_skip(data):
    """Обрабатывает 'пропусти' — пропускает текущее упражнение целиком
    без записи подходов, через session.skip_exercise."""
    skipped, next_ex = sess.skip_exercise(data)
    if skipped is None:
        return "Сейчас нет активного упражнения, которое можно пропустить."
    if next_ex is None:
        return f"Пропустил «{skipped['name']}» — это было последнее упражнение дня. Напиши «закончил»."
    return f"Пропустил «{skipped['name']}».\n\nСледующее упражнение:\n{prog.format_exercise_line(next_ex)}"


def handle_undo(data):
    """Обрабатывает 'отмени' — откатывает последнюю записанную запись
    через session.undo_last_set."""
    undone = sess.undo_last_set(data)
    if undone is None:
        return "Нечего отменять — история пуста."
    weight_str = f"{undone['weight_kg']}кг" if undone.get("weight_kg") is not None else "б/в"
    return f"Отменил: {undone['exercise']} — {weight_str} \u00d7 {undone['reps']} (подход {undone['set_number']})."


def handle_cardio(data, text):
    """Обрабатывает 'кардио 5км' — отдельная команда, не привязана к
    пошаговому флоу тренировки, работает в любой момент разговора.
    Записывает под сегодняшней датой (не датой активной сессии, если
    она есть — кардио может быть до/после/вне тренировки вообще)."""
    km = sess.extract_cardio_km(text)
    if km is None:
        return "Не понял километраж — напиши, например, «кардио 5км»."

    today = datetime.now(timezone.utc).date().isoformat()
    w.add_cardio(data, today, km)
    total = w.get_cardio_for_date(data, today)
    return f"\U0001f6b4 Записал кардио: {km}км. Сегодня всего: {total['total_km']}км."


def handle_progress_request(data, text):
    """Обрабатывает 'покажи прогресс по X' — извлекает название через
    session.extract_progress_query, находит упражнение через
    workouts.find_exercise_by_partial_name (детерминированный substring-
    матчинг, не DeepSeek — простое сопоставление имени не требует LLM),
    строит отчёт через workouts.format_progress_report."""
    query = sess.extract_progress_query(text)
    if not query:
        return "Уточни, по какому упражнению показать прогресс — например «прогресс по жиму»."

    exercise = w.find_exercise_by_partial_name(data, query)
    if exercise is None:
        known = w.known_exercises(data)
        if not known:
            return "У тебя ещё нет истории тренировок — не с чем сравнивать прогресс."
        return (
            f"Не нашёл упражнение «{query}» однозначно, или таких несколько. "
            f"Есть в истории: {', '.join(known[:10])}."
        )

    report = w.format_progress_report(data, exercise)
    return report or f"Нет истории по «{exercise}»."


def handle_extend_rest(data, text):
    """Обрабатывает просьбу продлить отдых — 'продли на 30', 'устал'.
    Использует session.extract_extend_seconds для количества (дефолт
    30 сек, если число не указано явно) и session.extend_rest для
    самого продления."""
    extend_sec = sess.extract_extend_seconds(text)
    new_resting_until = sess.extend_rest(data, extend_sec)
    if new_resting_until is None:
        return "Сейчас нет активного отдыха, который можно продлить — начни подход или напиши «взял»."
    return f"\u23f8 Отдых продлён на {extend_sec} сек. Отдыхай, я напомню."


def handle_set_confirmation(data):
    """Обрабатывает 'взял' — записывает текущий подход через
    session.advance_position с параметрами ИЗ ПЛАНА (не спрашиваем вес
    у Антона каждый раз — он уже задан программой; если Антон делал не
    по плану, он напишет об этом текстом отдельно, не через 'взял').

    Возвращает СПИСОК текстов (не одну строку): если упражнение только
    что завершено (exercise_complete=True), первое сообщение — план/факт
    по всем подходам этого упражнения, второе — переход к следующему
    упражнению или предложение завершить день. Иначе — один элемент
    списка с подтверждением текущего подхода."""
    ex, set_num = sess.current_exercise_info(data)
    if ex is None:
        return ["Сейчас нет активной тренировки с планом — напиши «начал», чтобы я предложил план на сегодня."]

    weight = ex["weight_max_kg"] if ex["weight_max_kg"] is not None else None
    result = sess.advance_position(data, weight_kg=weight, reps=ex["reps_max"])

    messages = []

    if result["exercise_complete"]:
        completed = result["completed_exercise"]
        session = data["active_session"]
        normalized = w.normalize_exercise_name(completed["name"], data.get("exercise_aliases", {}))
        actual_sets = [
            s for s in data.get("sets", [])
            if s["exercise"] == normalized and s["date"] == session["date"]
        ]
        actual_sets.sort(key=lambda s: s["set_number"])
        messages.append(prog.format_exercise_plan_vs_fact(completed, actual_sets))

        # Прогрессия: пошаговый флоу ('взял') всегда пишет weight_max_kg/
        # reps_max из плана — не то, что реально поднято 'через силу'.
        # Поэтому rep_range берём ИЗ ЭТОГО упражнения (не общий дефолт
        # progression.py), и решение о прогрессии опирается не на 'достиг
        # ли верхней границы' (это всегда true в этом флоу по построению),
        # а на 'не было ли явного сигнала трудности' (RPE/note — их можно
        # добавить только текстом отдельно, не через короткое 'взял').
        suggestion = progression.suggest_progression(
            data, normalized, rep_range=(completed["reps_min"], completed["reps_max"])
        )
        if suggestion:
            suggestion_id = make_suggestion_id(normalized)
            data.setdefault("pending_suggestions", []).append({
                "id": suggestion_id,
                "exercise": normalized,
                "suggested_weight_kg": suggestion["suggested_weight_kg"],
                "suggested_reps": suggestion["suggested_reps"],
                "reasoning": suggestion["reasoning"],
                "message_id": None,
                "status": "pending",
                "created_ts": datetime.now(timezone.utc).isoformat(),
            })
            messages.append((suggestion_id, progression.format_suggestion_message(normalized, suggestion)))

    if result["day_complete"]:
        messages.append(
            "\U0001f3c1 Программа на сегодня завершена! Напиши «закончил», когда будешь готов увидеть итоги."
        )
        return messages

    next_ex = result["next_exercise"]
    if result["next_set_number"] == 1 and next_ex["order"] != ex["order"]:
        # перешли на новое упражнение
        messages.append(
            f"\u23f1 Отдых {result['rest_sec']} сек.\n\n"
            f"Следующее упражнение:\n{prog.format_exercise_line(next_ex)}\n\n"
            f"Когда будешь готов — напиши «взял»."
        )
        return messages

    # тот же подход, следующий номер
    messages.append(
        f"\u2705 Подход {set_num}/{ex['sets']} записан.\n"
        f"\u23f1 Отдых {result['rest_sec']} сек, потом подход {result['next_set_number']}/{ex['sets']}.\n"
        f"Напиши «взял», когда сделаешь."
    )
    return messages


def handle_callback(callback_data, data):
    """Обрабатывает нажатие кнопки подтверждения/отклонения предложения.
    Два типа предложений используют один и тот же pending_suggestions
    список, различаются префиксом callback_data:
    - 'sugg:...' — предложение прогрессии веса (progression.py)
    - 'repl:...' — предложение замены упражнения (parser.suggest_replacement)
    Возвращает текст для answerCallbackQuery (короткое всплывающее
    уведомление) или None, если callback_data не наш формат."""
    parts = callback_data.split(":")
    if len(parts) != 3 or parts[0] not in ("sugg", "repl"):
        return None
    prefix, action, suggestion_id = parts

    suggestions = data.get("pending_suggestions", [])
    match = next((s for s in suggestions if s["id"] == suggestion_id), None)
    if not match:
        return "Предложение уже неактуально"

    if match["status"] != "pending":
        return "Уже обработано"

    if prefix == "repl":
        if action == "confirm":
            sess.apply_replacement(data, match["order"], match["replacement"])
            match["status"] = "confirmed"
            return f"Заменено: {match['replacement']['name']}"
        elif action == "reject":
            match["status"] = "rejected"
            return "Понял, оставляю как есть"
        return None

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

    # 28.07.2026 ФИКС: раньше здесь был tg_get_updates(offset) — не
    # работает, пока активен webhook (Telegram API: getUpdates и webhook
    # взаимоисключающи, апдейты уходят только куда-то одно). Теперь
    # апдейт приходит готовым через repository_dispatch client_payload
    # (см. worker.js и fitness_bot.yml) — GitHub Actions кладёт его в
    # переменную окружения как JSON-строку.
    raw_update = os.environ.get("TELEGRAM_UPDATE_JSON", "")
    if not raw_update or raw_update == "null":
        # workflow_dispatch (ручной запуск без реального апдейта) —
        # безопасно ничего не делать, не падать. Полезно для проверки,
        # что сам CI-прогон вообще работает.
        print("No update payload — manual run or empty dispatch, nothing to do.")
        return 0

    try:
        u = json.loads(raw_update)
    except json.JSONDecodeError as e:
        print(f"  ! failed to parse TELEGRAM_UPDATE_JSON: {e}", file=sys.stderr)
        return 1

    updates = [u]  # старый код ниже работает со списком апдейтов — оставляем форму

    outgoing = []  # список (text, reply_markup_or_None) для отправки после обработки всех апдейтов

    for u in updates:
        cb = u.get("callback_query")
        if cb:
            result_text = handle_callback(cb.get("data", ""), data)
            tg_answer_callback(cb.get("id", ""), result_text or "")
            continue

        msg = u.get("message") or {}
        text = msg.get("text", "")
        if not text or text.startswith("/start"):
            continue

        if sess.is_awaiting_weight_input(data):
            outgoing.append((handle_weight_answer(data, text), None, None))
            continue

        if sess.is_awaiting_wellness_input(data):
            outgoing.append((handle_wellness_answer(data, text), None, None))
            continue

        if sess.is_progress_request(text):
            outgoing.append((handle_progress_request(data, text), None, None))
            continue

        if sess.is_session_start(text):
            outgoing.append((handle_session_start(data), None, None))
            continue

        if sess.is_session_end(text):
            session_result = sess.end_session(data)
            if session_result is None:
                outgoing.append(("Тренировка не была начата — нечего завершать.", None, None))
            else:
                report = sess.build_session_report(
                    data,
                    session_result["exercises"],
                    session_result["date"],
                    day_id=session_result["day_id"],
                    body_weight_kg=session_result["body_weight_kg"],
                    duration_minutes=session_result["duration_minutes"],
                    sleep_hours=session_result["sleep_hours"],
                    stress_level=session_result["stress_level"],
                )
                outgoing.append((report, None, None))
            continue

        if sess.is_replace_exercise_request(text):
            reply_text, markup = handle_replace_request(data, text)
            outgoing.append((reply_text, markup, None))
            continue

        if sess.is_skip_request(text):
            outgoing.append((handle_skip(data), None, None))
            continue

        if sess.is_undo_request(text):
            outgoing.append((handle_undo(data), None, None))
            continue

        if sess.is_cardio_message(text):
            outgoing.append((handle_cardio(data, text), None, None))
            continue

        if sess.is_extend_rest_request(text):
            outgoing.append((handle_extend_rest(data, text), None, None))
            continue

        if sess.is_set_confirmation(text):
            for item in handle_set_confirmation(data):
                if isinstance(item, tuple):
                    suggestion_id, msg_text = item
                    outgoing.append((msg_text, suggestion_keyboard(suggestion_id), suggestion_id))
                else:
                    outgoing.append((item, None, None))
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
