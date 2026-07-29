"""Хранилище тренировок — load/save, добавление сетов, история по
упражнению, работа с targets и pending_suggestions.

Схема данных описана в SCHEMA.md — читать перед правкой этого файла.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKOUTS_PATH = os.path.join(ROOT, "workouts.json")


def load_workouts():
    if not os.path.exists(WORKOUTS_PATH):
        return {"schema_version": 1, "sets": [], "exercise_aliases": {},
                "pending_suggestions": [], "targets": {}, "wellness_log": {}, "cardio_log": {},
                "active_phase": {"phase_id": "volume", "started_date": None, "reminder_sent": False},
                "weight_log": {}, "skipped_days": {}}
    with open(WORKOUTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
        data.setdefault("wellness_log", {})  # существующие файлы без этого поля не должны падать
        data.setdefault("cardio_log", {})
        data.setdefault("active_phase", {"phase_id": "volume", "started_date": None, "reminder_sent": False})
        data.setdefault("weight_log", {})
        data.setdefault("skipped_days", {})
        return data


def save_workouts(data):
    with open(WORKOUTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_exercise_name(raw_name, aliases):
    """Сверяет raw_name с известными алиасами, возвращает нормализованное
    имя. Если raw_name уже совпадает с каким-то нормализованным именем
    (ключ в aliases) или с одним из его алиасов (substring match, ниж.
    регистр) — возвращает это нормализованное имя. Иначе возвращает
    raw_name как есть (новое упражнение, будет добавлено при первой
    записи через add_set)."""
    name_lower = raw_name.strip().lower()
    if name_lower in aliases:
        return name_lower
    for normalized, alias_list in aliases.items():
        if name_lower == normalized.lower():
            return normalized
        for alias in alias_list:
            if name_lower == alias.lower():
                return normalized
    return name_lower


def make_set_id(exercise, date, set_number):
    raw = f"{exercise}|{date}|{set_number}|{datetime.now(timezone.utc).isoformat()}"
    return "s_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def add_set(data, exercise_raw, date, weight_kg, reps, set_number,
            rpe=None, note="", safety_status="ok"):
    """Добавляет один сет в data['sets'], нормализуя имя упражнения через
    exercise_aliases. Если exercise_raw — новая формулировка известного
    упражнения, вызывающий код должен был уже обновить aliases (это
    делает parser.py через DeepSeek-сверку, не эта функция — add_set
    только записывает, не решает про алиасы).

    Возвращает добавленную запись (dict), также уже добавленную в
    data['sets'] по ссылке (мутирует data)."""
    exercise = normalize_exercise_name(exercise_raw, data.get("exercise_aliases", {}))
    entry = {
        "id": make_set_id(exercise, date, set_number),
        "date": date,
        "exercise": exercise,
        "exercise_raw": exercise_raw,
        "weight_kg": weight_kg,
        "reps": reps,
        "set_number": set_number,
        "rpe": rpe,
        "note": note,
        "safety_status": safety_status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    data.setdefault("sets", []).append(entry)
    return entry


def get_history_for_exercise(data, exercise, limit_sessions=10):
    """Возвращает сеты для нормализованного имени exercise, сгруппированные
    по дате тренировки, последние limit_sessions тренировок (не сетов —
    сессий). Формат: [{"date": "2026-07-28", "sets": [...]}], отсортировано
    по дате по возрастанию (старые первыми, для трендов проще читать
    хронологически)."""
    matching = [s for s in data.get("sets", []) if s["exercise"] == exercise]
    by_date = {}
    for s in matching:
        by_date.setdefault(s["date"], []).append(s)
    dates_sorted = sorted(by_date.keys())[-limit_sessions:]
    return [{"date": d, "sets": sorted(by_date[d], key=lambda x: x["set_number"])}
            for d in dates_sorted]


def get_target(data, exercise):
    """Текущая цель по упражнению (вес/повторы на следующую тренировку),
    None если ещё не установлена."""
    return data.get("targets", {}).get(exercise)


def save_wellness_for_date(data, date, sleep_hours, stress_level):
    """Сохраняет дневник самочувствия для конкретной даты тренировки —
    ПОСТОЯННОЕ хранилище (data['wellness_log']), в отличие от
    session.py's active_session['sleep_hours']/['stress_level'], которые
    живут только пока сессия открыта и теряются после end_session.

    Нужно для progression.py: чтобы решить, предлагать ли прогрессию,
    нужно знать самочувствие КОНКРЕТНОЙ прошедшей тренировки (по дате),
    не только текущей активной сессии."""
    data.setdefault("wellness_log", {})[date] = {
        "sleep_hours": sleep_hours,
        "stress_level": stress_level,
    }


def get_wellness_for_date(data, date):
    """Возвращает {"sleep_hours": ..., "stress_level": ...} для даты,
    или None если для этой даты дневник не заполнялся (тренировка была
    до появления этой фичи, или Антон ответил без чисел свободным
    текстом типа 'нормально' — в таком случае save_wellness_for_date
    всё равно вызывается с обоими None, что отличается от отсутствия
    записи вообще: 'заполнил, но не дал числа' vs 'не заполнял'."""
    return data.get("wellness_log", {}).get(date)


def add_cardio(data, date, km, ts=None):
    """Добавляет запись кардио на дату — СПИСОК (не одно значение),
    потому что кардио в программе логируется дважды за тренировку
    (5-7 км до, 8-10 км после, см. training_program.json['cardio']).
    Каждая команда 'кардио 5км' добавляет отдельную запись, не
    перезаписывает предыдущую за этот же день."""
    if ts is None:
        ts = datetime.now(timezone.utc).isoformat()
    data.setdefault("cardio_log", {}).setdefault(date, []).append({"km": km, "ts": ts})


def get_cardio_for_date(data, date):
    """Возвращает список записей кардио за дату ([] если ничего не
    записано), и их суммарный километраж отдельным полем для удобства
    вызывающего кода. Формат: {"entries": [...], "total_km": float}."""
    entries = data.get("cardio_log", {}).get(date, [])
    total_km = sum(e["km"] for e in entries)
    return {"entries": entries, "total_km": round(total_km, 1)}


def save_weight_for_date(data, date, weight_kg):
    """Сохраняет вес тела для конкретной даты — ПОСТОЯННОЕ хранилище
    (data['weight_log']), в отличие от session.py's
    active_session['body_weight_kg'], который живёт только пока сессия
    открыта и теряется после end_session. Нужно для трекера цели по
    весу — динамика/темп требуют истории по датам, не только 'текущий
    вес прямо сейчас'.

    Если на эту дату уже есть запись (несколько тренировок в один
    день — маловероятно, но возможно), перезаписывает последней —
    вес тела логично считать один раз в день, не суммировать."""
    data.setdefault("weight_log", {})[date] = weight_kg


def get_weight_history(data, limit_entries=None):
    """Возвращает список (date, weight_kg) пар, отсортированных по
    дате по возрастанию (старые первыми). limit_entries=None -> вся
    история, иначе последние N записей."""
    entries = sorted(data.get("weight_log", {}).items())
    if limit_entries is not None:
        entries = entries[-limit_entries:]
    return entries


def mark_day_skipped(data, date, reason=""):
    """Помечает конкретную дату как явно пропущенную (болезнь, нет
    времени и т.п.) — не требует активной сессии тренировки. Нужно,
    чтобы should_send_daily_reminder (session.py) не напоминал про
    тренировку, которую Антон уже явно отменил на сегодня."""
    data.setdefault("skipped_days", {})[date] = {"reason": reason}


def is_day_skipped(data, date):
    """True, если дата явно помечена как пропущенная через
    mark_day_skipped."""
    return date in data.get("skipped_days", {})


def format_weight_goal_report(data, profile):
    """Строит отчёт о прогрессе к цели по весу тела: текущий вес
    (последняя запись в истории), сколько осталось до цели, ФАКТИЧЕСКИЙ
    темп (кг/неделю — считается по первой и последней записи истории,
    не выдуманное число), прогноз даты достижения цели по этому реальному
    темпу.

    profile — dict с target_weight_kg/target_date/weekly_loss_target_kg
    (передаётся параметром, не читается из safety_constraints.json
    напрямую — держит workouts.py независимым модулем данных, не
    привязанным к safety.py).

    Возвращает None, если истории веса нет вообще (не с чем считать)."""
    history = get_weight_history(data)
    if not history:
        return None

    first_date, first_weight = history[0]
    last_date, last_weight = history[-1]
    target = profile["target_weight_kg"]

    lines = [f"\U0001f3af <b>Цель по весу:</b> {target} кг к {profile['target_date']}"]
    lines.append(f"Текущий вес: {last_weight} кг (запись от {last_date})")
    lines.append(f"Осталось: {round(last_weight - target, 1)} кг")

    if len(history) < 2:
        lines.append("Пока только одна запись — темп появится после следующего взвешивания.")
        return "\n".join(lines)

    days_elapsed = (datetime.fromisoformat(last_date) - datetime.fromisoformat(first_date)).days
    if days_elapsed <= 0:
        lines.append("Все записи за один день — темп появится позже.")
        return "\n".join(lines)

    weeks_elapsed = days_elapsed / 7
    actual_weekly_loss = (first_weight - last_weight) / weeks_elapsed
    lines.append(f"Фактический темп: {round(actual_weekly_loss, 2)} кг/неделю "
                 f"(план: {profile['weekly_loss_target_kg']} кг/неделю)")

    if actual_weekly_loss > 0:
        weeks_to_target = (last_weight - target) / actual_weekly_loss
        if weeks_to_target > 0:
            projected_date = datetime.fromisoformat(last_date) + timedelta(weeks=weeks_to_target)
            lines.append(f"При таком темпе цель — примерно {projected_date.date().isoformat()}")
        else:
            lines.append("Цель уже достигнута по текущему весу!")
    elif actual_weekly_loss < 0:
        lines.append("\u26a0\ufe0f Вес растёт, не снижается — при текущем темпе цель не будет достигнута.")
    else:
        lines.append("Вес не меняется — темп 0, прогноз даты недоступен.")

    return "\n".join(lines)


def format_period_summary(data, days, now=None):
    """Строит сводку за период (последние `days` календарных дней,
    включая сегодня): сколько дней с реальными тренировками, сколько
    явных пропусков, общий тоннаж, общее кардио, среднее самочувствие
    (сон/стресс), если заполнялось хоть раз за период.

    Период считается по календарным дням от (сегодня - days + 1) до
    сегодня включительно — не по количеству тренировок, а по факту
    времени, поэтому 'дней тренировок' может быть меньше, чем
    тренировочных дней по расписанию в этом диапазоне (если что-то
    пропущено и не помечено явно через mark_day_skipped)."""
    if now is None:
        now = datetime.now(timezone.utc)
    period_start = (now - timedelta(days=days - 1)).date()
    period_end = now.date()
    period_dates = {(period_start + timedelta(days=i)).isoformat() for i in range((period_end - period_start).days + 1)}

    sets_in_period = [s for s in data.get("sets", []) if s["date"] in period_dates]
    training_dates = sorted({s["date"] for s in sets_in_period})
    skipped_in_period = [d for d in data.get("skipped_days", {}) if d in period_dates]

    total_tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in sets_in_period)

    total_cardio_km = 0.0
    for date in period_dates:
        cardio = get_cardio_for_date(data, date)
        total_cardio_km += cardio["total_km"]

    sleep_values = []
    stress_values = []
    for date in training_dates:
        wellness = get_wellness_for_date(data, date)
        if wellness:
            if wellness.get("sleep_hours") is not None:
                sleep_values.append(wellness["sleep_hours"])
            if wellness.get("stress_level") is not None:
                stress_values.append(wellness["stress_level"])

    period_name = "неделю" if days == 7 else "месяц" if days == 30 else f"{days} дней"
    lines = [f"\U0001f4c8 <b>Итоги за {period_name}</b> ({period_start.isoformat()} — {period_end.isoformat()})\n"]
    lines.append(f"Тренировок: {len(training_dates)}")
    if skipped_in_period:
        lines.append(f"Пропусков (отмечено явно): {len(skipped_in_period)}")
    lines.append(f"Общий тоннаж: {round(total_tonnage)} кг")
    if total_cardio_km > 0:
        lines.append(f"Кардио: {round(total_cardio_km, 1)} км")
    if sleep_values:
        lines.append(f"Средний сон: {round(sum(sleep_values) / len(sleep_values), 1)}ч")
    if stress_values:
        lines.append(f"Средний стресс: {round(sum(stress_values) / len(stress_values), 1)}/10")

    return "\n".join(lines)


def get_active_phase(data):
    """Возвращает {"phase_id": ..., "started_date": ..., "reminder_sent":
    ...} — текущий блок периодизации. По умолчанию 'volume' (базовый
    план программы, без модификаторов) с started_date=None, если фаза
    никогда не менялась явно."""
    return data.get("active_phase", {"phase_id": "volume", "started_date": None, "reminder_sent": False})


def set_active_phase(data, phase_id, started_date):
    """Устанавливает активный блок периодизации ('strength'/'volume'/
    'deficit') с датой начала — нужна для отсчёта 6 недель на
    напоминание сменить блок. reminder_sent сбрасывается в False —
    новый блок начинает новый отсчёт, старое напоминание неактуально."""
    data["active_phase"] = {"phase_id": phase_id, "started_date": started_date, "reminder_sent": False}


def mark_phase_reminder_sent(data):
    """Помечает, что напоминание о смене блока (истёк 6-недельный срок)
    отправлено — не даёт Cron Trigger'у слать его повторно каждый день
    после истечения срока, пока блок не сменится явно (set_active_phase
    сбрасывает флаг заново)."""
    phase = data.get("active_phase")
    if phase:
        phase["reminder_sent"] = True


def format_progress_report(data, exercise, limit_sessions=10):
    """Строит текстовый отчёт прогресса по exercise за последние
    limit_sessions тренировок — по каждой сессии: дата, макс. вес,
    суммарный тоннаж; в конце — изменение тоннажа от первой к последней
    сессии в процентах (тот же принцип, что session._format_trend, но
    за весь диапазон, не только последние 2 сессии).

    Возвращает None, если истории по этому упражнению нет вообще —
    вызывающий код должен явно обработать этот случай (переспросить,
    может, опечатка в названии), не показывать пустой отчёт."""
    history = get_history_for_exercise(data, exercise, limit_sessions=limit_sessions)
    if not history:
        return None

    lines = [f"\U0001f4c8 <b>Прогресс: {exercise}</b>\n"]
    for session in history:
        sets = session["sets"]
        max_weight = max((s.get("weight_kg") or 0) for s in sets)
        tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in sets)
        max_reps = max(s.get("reps", 0) for s in sets)
        weight_str = f"{max_weight}кг" if max_weight else "б/в"
        lines.append(f"  {session['date']}: {weight_str} \u00d7 {max_reps} (тоннаж {round(tonnage)} кг)")

    if len(history) >= 2:
        first_tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in history[0]["sets"])
        last_tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in history[-1]["sets"])
        if first_tonnage > 0:
            change_pct = round((last_tonnage - first_tonnage) / first_tonnage * 100)
            sign = "+" if change_pct >= 0 else ""
            lines.append(f"\nИзменение тоннажа за {len(history)} тренировок: {sign}{change_pct}%")

    return "\n".join(lines)


def set_target(data, exercise, weight_kg, reps):
    data.setdefault("targets", {})[exercise] = {
        "weight_kg": weight_kg,
        "reps": reps,
        "set_at": datetime.now(timezone.utc).date().isoformat(),
    }


def add_alias(data, normalized_name, new_alias):
    """Добавляет новую формулировку new_alias как алиас к normalized_name.
    Идемпотентно — повторное добавление того же алиаса не дублирует."""
    aliases = data.setdefault("exercise_aliases", {})
    existing = aliases.setdefault(normalized_name, [])
    if new_alias.lower() not in [a.lower() for a in existing] and new_alias.lower() != normalized_name.lower():
        existing.append(new_alias)


def known_exercises(data):
    """Список всех нормализованных имён упражнений, встречавшихся хоть раз."""
    return sorted({s["exercise"] for s in data.get("sets", [])})


def find_exercise_by_partial_name(data, query):
    """Ищет упражнение в known_exercises по частичному совпадению текста
    query (например, 'жим' должно найти 'жим лёжа гантели'). Совпадение
    в ОБЕ стороны (query в имени ИЛИ имя в query) — на случай, если
    пользователь написал длиннее или короче реального названия.

    Возвращает нормализованное имя (str) или None, если:
    - совпадений нет вообще
    - совпадений НЕСКОЛЬКО и они неоднозначны (не возвращаем наугад
      первое попавшееся — лучше честно сказать 'уточни', чем показать
      прогресс не того упражнения)."""
    q = query.strip().lower()
    if not q:
        return None

    matches = [
        ex for ex in known_exercises(data)
        if q in ex or ex in q
    ]

    if len(matches) == 1:
        return matches[0]
    return None
