"""Определение начала/конца тренировочной сессии + отчёт по её итогам.

Распознавание начала/конца — ДЕТЕРМИНИРОВАННОЕ (простой keyword match),
не через DeepSeek. Это осознанное решение: "начинаю тренировку" не
требует понимания сложного контекста, где LLM даёт преимущество —
точный список слов надёжнее, предсказуемее и мгновеннее, а неверное
распознавание старта/конца сессии портит весь последующий отчёт.

Session state хранится в workouts.json (data['active_session']) — не
отдельным файлом, чтобы не рассинхронизировать с основным состоянием
при параллельных прогонах.
"""
import re
from datetime import datetime, timedelta, timezone

import calories as cal
import program as prog
import workouts as w

START_KEYWORDS = ["начал", "начинаю", "старт", "погнали", "поехали тренир"]
END_KEYWORDS = ["закончил", "конец тренировки", "финиш", "всё, закончили", "готово с тренировкой"]
SET_DONE_KEYWORDS = ["взял", "готово", "сделал", "есть"]
EXTEND_REST_KEYWORDS = ["продли", "ещё минут", "ещё секунд", "устал", "нужно больше", "добавь врем"]


def is_session_start(text):
    t = text.strip().lower()
    return any(kw in t for kw in START_KEYWORDS)


def is_session_end(text):
    t = text.strip().lower()
    return any(kw in t for kw in END_KEYWORDS)


def is_set_confirmation(text):
    """Короткое подтверждение 'подход сделан' в пошаговом флоу — 'взял',
    'готово', 'сделал', 'есть'. Ограничение по длине (<=4 слова) намеренно:
    без него 'сделал присед 50 на 8, тяжело пошёл' тоже совпало бы (там
    есть слово 'сделал'), но это полноценная запись подхода с деталями,
    не короткое 'продолжай' — их нельзя путать, у них разная обработка."""
    t = text.strip().lower()
    if len(t.split()) > 4:
        return False
    return any(kw in t for kw in SET_DONE_KEYWORDS)


def is_extend_rest_request(text):
    """'продли отдых', 'ещё минуту', 'устал, нужно больше времени' —
    просьба продлить текущий таймер отдыха. Тоже детерминированно, не
    DeepSeek: короткая команда, требующая мгновенной реакции, не
    сложного понимания контекста."""
    t = text.strip().lower()
    return any(kw in t for kw in EXTEND_REST_KEYWORDS)


def parse_weight_kg(text):
    """Извлекает число (вес в кг) из ответа на вопрос 'сколько сейчас
    весишь' — '121', '121.5', '121,5 кг', 'где-то 120'. Детерминированно
    (regex), не DeepSeek: простое число, не требует понимания контекста.
    Возвращает float или None, если число не найдено."""
    match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_extend_seconds(text, default_sec=30):
    """Вытаскивает число секунд из просьбы продлить отдых.
    'продли на 30' -> 30, 'ещё минуту' -> 60, 'ещё 2 минуты' -> 120,
    просто 'устал' (без числа) -> default_sec (разумный дефолт, не
    заставляем формулировать точно, когда и так тяжело)."""
    t = text.strip().lower()

    if "минут" in t:
        match = re.search(r"(\d+)\s*минут", t)
        minutes = int(match.group(1)) if match else 1  # "минуту" без цифры -> 1
        return minutes * 60

    match = re.search(r"(\d+)\s*сек", t)
    if match:
        return int(match.group(1))

    # просто число без единиц ("продли на 45") — считаем секундами,
    # это откликается на короткий отдых 45-90 сек в программе
    match = re.search(r"(\d+)", t)
    if match:
        return int(match.group(1))

    return default_sec


def start_session(data, day_id=None):
    """Открывает сессию — запоминает момент старта, дату, день программы
    (по расписанию через program.today_day_id(), если day_id не передан
    явно) и позицию для пошагового флоу (первое упражнение, первый
    подход). Идемпотентно: повторный вызов при уже открытой сессии не
    создаёт вторую, просто ничего не делает.

    day_id можно передать явно (например, если сегодня день отдыха по
    расписанию, но Антон всё равно хочет потренироваться на замену) —
    вызывающий код в bot.py решает, спрашивать ли об этом.

    awaiting_weight_input=True сразу после старта (если day_id задан —
    для дня отдыха без плана вес спрашивать бессмысленно) — bot.py
    должен сначала спросить вес до тренировки и получить ответ, прежде
    чем показывать план/вести по упражнениям. Нужен для расчёта тоннажа
    тела (вес тела учитывается в калориях) и трекинга динамики веса."""
    if data.get("active_session"):
        return False  # уже открыта
    data["active_session"] = {
        "started_ts": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).date().isoformat(),
        "day_id": day_id,
        "current_exercise_order": 1 if day_id else None,
        "current_set_number": 1 if day_id else None,
        "resting_until": None,
        "awaiting_weight_input": bool(day_id),
        "body_weight_kg": None,
    }
    return True


def current_exercise_info(data):
    """Возвращает (exercise_dict, set_number) для текущей позиции
    активной сессии, или (None, None) если сессия не активна, день не
    определён, или упражнения в дне кончились (день пройден)."""
    session = data.get("active_session")
    if not session or not session.get("day_id"):
        return None, None
    order = session.get("current_exercise_order")
    if order is None:
        return None, None
    ex = prog.get_exercise(session["day_id"], order)
    if not ex:
        return None, None  # order вышел за пределы дня — упражнения кончились
    return ex, session.get("current_set_number", 1)


def rest_timer_expired(data, now=None):
    """True, если активная сессия ждёт следующего подхода, отдых уже
    истёк (resting_until в прошлом), и напоминание об этом ЕЩЁ НЕ
    отправлено (reminder_sent=False). Используется Cron Trigger'ом
    (см. cloudflare-worker/ + timer.py) — вызывается раз в минуту,
    должен быть дешёвым и не иметь побочных эффектов сам по себе
    (пометка reminder_sent=True — отдельный шаг, mark_reminder_sent,
    вызывающий код должен явно его выполнить после реальной отправки)."""
    session = data.get("active_session")
    if not session or not session.get("resting_until"):
        return False
    if session.get("reminder_sent"):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    resting_until = datetime.fromisoformat(session["resting_until"])
    return now >= resting_until


def mark_reminder_sent(data):
    """Помечает, что напоминание об истёкшем отдыхе отправлено — не
    даёт Cron Trigger'у слать его повторно на следующих прогонах, пока
    не начнётся новый отдых (advance_position сбрасывает флаг заново)."""
    session = data.get("active_session")
    if session:
        session["reminder_sent"] = True


def extend_rest(data, extend_sec):
    """Продлевает текущий отдых на extend_sec. Возвращает новое
    resting_until (str ISO) или None, если нет активного отдыха
    (сессия не активна, или ещё ни одного подхода не сделано).

    Продление считается от МАКСИМУМА(текущий resting_until, сейчас) —
    не просто resting_until + extend_sec. Если Антон пишет 'продли',
    когда таймер уже истёк (например, напоминание уже пришло, но он
    ещё не готов), продление от старого resting_until в прошлом дало
    бы неверный результат — таймер оказался бы уже истёкшим на момент
    продления. Продлеваем от 'сейчас', если сейчас позже, чем плановое
    resting_until.

    Также сбрасывает reminder_sent=False — если напоминание уже было
    отправлено, а Антон просит продлить, значит фактически отдых ещё
    не закончился для него, и повторное напоминание должно прийти снова
    после нового (продлённого) resting_until."""
    session = data.get("active_session")
    if not session or not session.get("resting_until"):
        return None

    now = datetime.now(timezone.utc)
    current_resting_until = datetime.fromisoformat(session["resting_until"])
    base = max(now, current_resting_until)
    new_resting_until = base + timedelta(seconds=extend_sec)

    session["resting_until"] = new_resting_until.isoformat()
    session["reminder_sent"] = False
    return session["resting_until"]


def advance_position(data, weight_kg, reps, rpe=None, note=""):
    """Записывает выполненный сет текущего упражнения через
    workouts.add_set, продвигает позицию сессии на следующий подход
    или следующее упражнение.

    Возвращает dict с ключами:
        "recorded_exercise": имя записанного упражнения (или None,
            если сессия/позиция невалидна — ничего не записано)
        "exercise_complete": True, если это был ПОСЛЕДНИЙ подход
            текущего упражнения (неважно, есть ли следующее упражнение
            в дне или это конец дня) — сигнал для bot.py показать
            план/факт статистику по только что завершённому упражнению
        "completed_exercise": полный dict упражнения из программы
            (для plan/fact сравнения) — совпадает с ex, если
            exercise_complete=True, иначе None
        "day_complete": True, если это был последний сет последнего
            упражнения дня — вызывающий код может предложить сразу
            завершить сессию
        "next_exercise": следующее упражнение (dict) или None, если
            день завершён
        "next_set_number": номер следующего подхода в next_exercise,
            актуален только если next_exercise задан и это ТО ЖЕ
            упражнение (не первый подход нового)
        "rest_sec": сколько отдыхать после записанного сета — берётся
            из ТОЛЬКО ЧТО записанного упражнения (rest после ЭТОГО
            подхода), не следующего
    """
    ex, set_number = current_exercise_info(data)
    if ex is None:
        return {"recorded_exercise": None, "exercise_complete": False,
                "completed_exercise": None, "day_complete": False,
                "next_exercise": None, "next_set_number": None, "rest_sec": None}

    session = data["active_session"]
    day_id = session["day_id"]

    w.add_set(data, ex["name"], session["date"], weight_kg, reps,
              set_number, rpe=rpe, note=note)

    rest_sec = ex["rest_sec"]

    # Таймер отдыха: Cron Trigger (см. cloudflare-worker/, каждую минуту)
    # сравнивает resting_until с текущим временем и шлёт напоминание,
    # если время истекло и напоминание ещё не отправлено. reminder_sent
    # сбрасывается в False на каждый новый подход — без этого напоминание
    # ушло бы повторно на следующей минуте после того, как уже сработало.
    session["resting_until"] = (datetime.now(timezone.utc) + timedelta(seconds=rest_sec)).isoformat()
    session["reminder_sent"] = False

    if set_number < ex["sets"]:
        # ещё есть подходы в этом же упражнении
        session["current_set_number"] = set_number + 1
        return {
            "recorded_exercise": ex["name"], "exercise_complete": False,
            "completed_exercise": None, "day_complete": False,
            "next_exercise": ex, "next_set_number": set_number + 1,
            "rest_sec": rest_sec,
        }

    # подходы этого упражнения закончились — следующее упражнение в дне
    next_order = ex["order"] + 1
    next_ex = prog.get_exercise(day_id, next_order)
    if next_ex is None:
        # это было последнее упражнение дня
        return {
            "recorded_exercise": ex["name"], "exercise_complete": True,
            "completed_exercise": ex, "day_complete": True,
            "next_exercise": None, "next_set_number": None,
            "rest_sec": rest_sec,
        }

    session["current_exercise_order"] = next_order
    session["current_set_number"] = 1
    return {
        "recorded_exercise": ex["name"], "exercise_complete": True,
        "completed_exercise": ex, "day_complete": False,
        "next_exercise": next_ex, "next_set_number": 1,
        "rest_sec": rest_sec,
    }


def end_session(data):
    """Закрывает сессию, возвращает dict с данными для построения отчёта,
    либо None если сессия не была открыта — вызывающий код должен явно
    обработать этот случай.

    Возвращает dict (не tuple — сигнатура уже дважды росла при добавлении
    day_id, потом веса/калорий; словарь не потребует менять вызовы снова
    при следующем требовании):
        "exercises": список нормализованных имён упражнений сессии
        "date": дата сессии (YYYY-MM-DD)
        "day_id": id дня программы или None
        "body_weight_kg": вес тела, введённый в начале, или None
        "duration_minutes": продолжительность тренировки в минутах
    Все поля возвращаются ОТДЕЛЬНО от active_session, потому что
    active_session обнуляется тут же."""
    session = data.get("active_session")
    if not session:
        return None

    session_date = session["date"]
    today_sets = [s for s in data.get("sets", []) if s["date"] == session_date]
    exercises_today = sorted({s["exercise"] for s in today_sets})

    duration_minutes = cal.session_duration_minutes(session.get("started_ts"))

    result = {
        "exercises": exercises_today,
        "date": session_date,
        "day_id": session.get("day_id"),
        "body_weight_kg": session.get("body_weight_kg"),
        "duration_minutes": duration_minutes,
    }

    data["active_session"] = None
    return result


def is_session_active(data):
    return bool(data.get("active_session"))


def is_awaiting_weight_input(data):
    """True, если сессия открыта и ждёт ответа на вопрос о весе тела до
    тренировки — весь остальной флоу (план, подходы) должен подождать."""
    session = data.get("active_session")
    return bool(session and session.get("awaiting_weight_input"))


def set_body_weight(data, weight_kg):
    """Сохраняет вес тела для этой сессии, снимает флаг ожидания.
    Возвращает True, если сессия была активна и ждала веса, False если
    нет активной сессии (вызывающий код не должен был сюда попасть, но
    функция не падает на некорректном состоянии, просто ничего не делает)."""
    session = data.get("active_session")
    if not session:
        return False
    session["body_weight_kg"] = weight_kg
    session["awaiting_weight_input"] = False
    return True


def build_session_report(data, exercises, session_date, day_id=None,
                          body_weight_kg=None, duration_minutes=None):
    """Строит текст отчёта: по каждому упражнению сессии —
    (1) план/факт: сравнение фактических подходов с планом программы,
        если day_id известен и упражнение реально есть в плане этого
        дня (может не быть — например, если Антон записал что-то не
        по плану текстом отдельно);
    (2) тренд: сравнение с предыдущей тренировкой этого упражнения
        (тоннаж туда-сюда), если такая история есть.
    В конце — общий тоннаж тренировки (сумма вес×повторы по всем сетам)
    и оценка калорий (calories.estimate_calories, MET-формула), если
    body_weight_kg и duration_minutes заданы. Без них — эта секция не
    показывается, не выдумываем цифры на отсутствующих данных.

    day_id=None (например, если тренировка началась в день отдыха без
    явного плана) -> план/факт не показывается, только тренд, как было
    раньше — обратная совместимость с тренировками без day_id."""
    if not exercises:
        return "Тренировка завершена, но ни одного подхода не записано."

    lines = ["\U0001f3c1 <b>Тренировка завершена</b>\n"]
    day_plan = prog.get_day_plan(day_id) if day_id else None
    total_tonnage = 0.0

    for exercise in exercises:
        history = w.get_history_for_exercise(data, exercise, limit_sessions=5)
        today_sessions = [h for h in history if h["date"] == session_date]
        if not today_sessions:
            continue
        today_sets = today_sessions[0]["sets"]
        total_tonnage += sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in today_sets)

        plan_ex = None
        if day_plan:
            for ex in day_plan["exercises"]:
                if w.normalize_exercise_name(ex["name"], data.get("exercise_aliases", {})) == exercise:
                    plan_ex = ex
                    break

        if plan_ex:
            summary = prog.format_exercise_plan_vs_fact(plan_ex, today_sets)
        else:
            summary = _format_exercise_today(exercise, today_sets)

        prior_sessions = [h for h in history if h["date"] < session_date]
        if prior_sessions:
            trend = _format_trend(today_sets, prior_sessions[-1]["sets"])
            summary += f"\n  {trend}"
        lines.append(summary)

    totals = [f"\U0001f4ca <b>Итого тоннаж:</b> {round(total_tonnage)} кг"]
    calories = cal.estimate_calories(body_weight_kg, duration_minutes)
    if calories is not None:
        totals.append(f"\U0001f525 <b>Оценка калорий:</b> ~{calories} ккал "
                       f"(при весе {body_weight_kg} кг, {duration_minutes:.0f} мин)")
    lines.append("\n".join(totals))

    return "\n\n".join(lines)


def _format_exercise_today(exercise, today_sets):
    parts = []
    for s in today_sets:
        weight = f"{s['weight_kg']}\u00d7" if s.get("weight_kg") else ""
        parts.append(f"{weight}{s['reps']}")
    return f"<b>{exercise}</b>: " + ", ".join(parts)


def _format_trend(today_sets, prior_sets):
    """Сравнивает суммарный тоннаж (вес x повторы, просуммировано по
    сетам) сегодня против прошлой сессии — простая, понятная метрика
    тренда, не требующая сложной статистики."""
    today_volume = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in today_sets)
    prior_volume = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in prior_sets)

    if prior_volume == 0:
        return "первая тренировка с весом по этому упражнению"

    diff_pct = round((today_volume - prior_volume) / prior_volume * 100)
    if diff_pct > 0:
        return f"\U0001f4c8 тоннаж +{diff_pct}% к прошлой тренировке"
    elif diff_pct < 0:
        return f"\U0001f4c9 тоннаж {diff_pct}% к прошлой тренировке"
    return "тоннаж как в прошлый раз"
