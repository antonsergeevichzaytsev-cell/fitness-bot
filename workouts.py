"""Хранилище тренировок — load/save, добавление сетов, история по
упражнению, работа с targets и pending_suggestions.

Схема данных описана в SCHEMA.md — читать перед правкой этого файла.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKOUTS_PATH = os.path.join(ROOT, "workouts.json")


def load_workouts():
    if not os.path.exists(WORKOUTS_PATH):
        return {"schema_version": 1, "sets": [], "exercise_aliases": {},
                "pending_suggestions": [], "targets": {}, "wellness_log": {}}
    with open(WORKOUTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
        data.setdefault("wellness_log", {})  # существующие файлы без этого поля не должны падать
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
