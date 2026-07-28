"""Определение дня программы по дню недели + доступ к плану тренировки.

Расписание (из Anton_Training_Program.docx): Пн=День1, Ср=День2,
Пт=День3, Сб=День4, Вт/Чт/Вс=отдых. Это не эвристика и не DeepSeek —
жёсткое, детерминированное соответствие день недели -> программа.
"""
import json
import os
from datetime import datetime, timezone

import workouts as w

ROOT = os.path.dirname(os.path.abspath(__file__))
PROGRAM_PATH = os.path.join(ROOT, "training_program.json")

WEEKDAY_TO_DAY_ID = {
    0: "1",  # Monday
    2: "2",  # Wednesday
    4: "3",  # Friday
    5: "4",  # Saturday
}


def load_program():
    with open(PROGRAM_PATH, encoding="utf-8") as f:
        return json.load(f)


def today_day_id(now=None):
    """Возвращает id дня программы ('1'..'4') для сегодняшнего дня недели,
    или None если сегодня день отдыха (Вт/Чт/Вс) — вызывающий код должен
    явно обработать None, не молча брать какой-то день по умолчанию."""
    if now is None:
        now = datetime.now(timezone.utc)
    return WEEKDAY_TO_DAY_ID.get(now.weekday())


def get_day_plan(day_id, program=None):
    """Возвращает план дня (dict с name/weekday/exercises/...) или None,
    если day_id не существует в программе."""
    if program is None:
        program = load_program()
    return program.get("days", {}).get(day_id)


def get_exercise(day_id, order, program=None):
    """Возвращает конкретное упражнение по порядковому номеру в дне,
    или None если не найдено."""
    day = get_day_plan(day_id, program)
    if not day:
        return None
    for ex in day["exercises"]:
        if ex["order"] == order:
            return ex
    return None


def format_exercise_line(ex):
    """Одна строка с параметрами упражнения — общий формат для плана
    и для показа текущего упражнения в пошаговом флоу."""
    if ex["weight_min_kg"] is None:
        weight = "по ощущению"
    elif ex["weight_min_kg"] == ex["weight_max_kg"]:
        weight = f"{ex['weight_min_kg']}кг"
    else:
        weight = f"{ex['weight_min_kg']}-{ex['weight_max_kg']}кг"
    reps = (
        f"{ex['reps_min']}-{ex['reps_max']}" if ex["reps_min"] != ex["reps_max"]
        else str(ex["reps_min"])
    )
    per_side = " /сторона" if ex.get("per_side") else ""
    return (
        f"{ex['order']}. {ex['name']} ({ex['machine']})\n"
        f"   {ex['sets']} x {reps}{per_side}, {weight}, темп {ex['tempo']}, отдых {ex['rest_sec']}с"
    )


def format_day_plan(day_id, program=None):
    """Полный текст плана на день — список всех упражнений с параметрами.
    Показывает СТАТИЧНЫЙ план из training_program.json, БЕЗ учёта
    подтверждённых targets прогрессии (см. format_day_plan_with_targets
    для версии, которая их применяет) — эта функция чистая, не знает о
    состоянии workouts.json."""
    day = get_day_plan(day_id, program)
    if not day:
        return None
    lines = [f"<b>День {day_id} — {day['name']}</b>\n"]
    for ex in day["exercises"]:
        lines.append(format_exercise_line(ex))
    return "\n\n".join(lines)


def format_day_plan_with_targets(day_id, workouts_data, program=None):
    """То же, что format_day_plan, но применяет подтверждённые targets
    прогрессии (workouts.get_target) к каждому упражнению перед
    отображением — иначе план в начале тренировки показывал бы старый
    вес даже после того, как Антон подтвердил прогрессию на прошлой
    сессии (найдено 28.07.2026: target сохранялся, но никогда не
    отображался и не применялся при показе плана дня)."""
    day = get_day_plan(day_id, program)
    if not day:
        return None
    lines = [f"<b>День {day_id} — {day['name']}</b>\n"]
    for ex in day["exercises"]:
        normalized = w.normalize_exercise_name(ex["name"], workouts_data.get("exercise_aliases", {}))
        target = w.get_target(workouts_data, normalized)
        if target:
            ex = dict(ex)
            ex["weight_min_kg"] = target["weight_kg"]
            ex["weight_max_kg"] = target["weight_kg"]
        lines.append(format_exercise_line(ex))
    return "\n\n".join(lines)


def _set_status(ex, actual_weight_kg, actual_reps):
    """Сравнивает один фактический подход с диапазоном плана, возвращает
    (эмодзи, текст) статуса. Вес null в плане ('по ощущению') не
    сравнивается — только повторы."""
    reps_ok = ex["reps_min"] <= actual_reps <= ex["reps_max"]

    if ex["weight_min_kg"] is None:
        # план "по ощущению" — сравниваем только повторы
        return ("\u2705", "по плану") if reps_ok else ("\u26a0\ufe0f", "повторы вне плана")

    weight_ok = ex["weight_min_kg"] <= actual_weight_kg <= ex["weight_max_kg"]

    if weight_ok and reps_ok:
        return "\u2705", "по плану"
    if actual_weight_kg > ex["weight_max_kg"] or actual_reps > ex["reps_max"]:
        return "\U0001f4c8", "сверх плана"
    return "\U0001f4c9", "ниже плана"


def format_exercise_plan_vs_fact(ex, actual_sets):
    """План/факт по одному завершённому упражнению — построчно по
    каждому подходу: плановый диапазон vs фактически записанный
    вес/повторы, с эмодзи-статусом (по плану / сверх / ниже).

    actual_sets — список записей из workouts.py (data['sets'],
    отфильтрованных по этому упражнению и сегодняшней дате), в порядке
    set_number по возрастанию. Может быть короче ex['sets'], если
    упражнение не завершено (вызывающий код обычно вызывает это только
    когда exercise_complete=True, но функция не требует этого сама)."""
    if ex["weight_min_kg"] is None:
        plan_weight = "по ощущению"
    elif ex["weight_min_kg"] == ex["weight_max_kg"]:
        plan_weight = f"{ex['weight_min_kg']}кг"
    else:
        plan_weight = f"{ex['weight_min_kg']}-{ex['weight_max_kg']}кг"
    plan_reps = (
        f"{ex['reps_min']}-{ex['reps_max']}" if ex["reps_min"] != ex["reps_max"]
        else str(ex["reps_min"])
    )

    lines = [f"<b>{ex['name']}</b> — план: {ex['sets']} x {plan_reps}, {plan_weight}\n"]
    for s in actual_sets:
        emoji, status = _set_status(ex, s.get("weight_kg") or 0, s.get("reps", 0))
        weight_str = f"{s['weight_kg']}кг" if s.get("weight_kg") is not None else "б/в"
        lines.append(f"  Подход {s['set_number']}: {weight_str} \u00d7 {s['reps']} {emoji} {status}")

    return "\n".join(lines)

