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
                "pending_suggestions": [], "targets": {}}
    with open(WORKOUTS_PATH, encoding="utf-8") as f:
        return json.load(f)


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
