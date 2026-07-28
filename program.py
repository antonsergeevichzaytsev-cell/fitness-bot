"""Определение дня программы по дню недели + доступ к плану тренировки.

Расписание (из Anton_Training_Program.docx): Пн=День1, Ср=День2,
Пт=День3, Сб=День4, Вт/Чт/Вс=отдых. Это не эвристика и не DeepSeek —
жёсткое, детерминированное соответствие день недели -> программа.
"""
import json
import os
from datetime import datetime, timezone

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
    """Полный текст плана на день — список всех упражнений с параметрами."""
    day = get_day_plan(day_id, program)
    if not day:
        return None
    lines = [f"<b>День {day_id} — {day['name']}</b>\n"]
    for ex in day["exercises"]:
        lines.append(format_exercise_line(ex))
    return "\n\n".join(lines)
