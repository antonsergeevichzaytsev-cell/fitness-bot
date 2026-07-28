"""Проверка реалистичности распарсенного веса/повторов — ловит вероятные
опечатки ('500' вместо '50') до записи в историю, не мешая легитимной
прогрессии (+2.5-5кг — нормальное дело, не должно триггерить переспрос).

Применяется ТОЛЬКО к свободному тексту (handle_workout_message) —
пошаговый флоу 'взял' берёт вес/повторы из плана программы, там
опечаток пользователя физически быть не может.

Источник эталона (в порядке приоритета):
1. Диапазон из training_program.json, если упражнение есть в текущей
   программе (day/order неизвестны на этом этапе — ищем по всем 4
   дням, раз одно упражнение может встречаться в нескольких).
2. Последняя запись в истории этого упражнения (workouts.json), если
   плана нет — сравниваем с тем, что реально делал раньше.
3. Если нет ни плана, ни истории — эталона нет, проверка не срабатывает
   (первая запись нового упражнения не с чем сравнивать, не блокируем).
"""
import program as prog
import workouts as w

# Множитель отклонения, при превышении которого считаем число подозрительным.
# 2.0 означает: вес больше чем в 2 раза выше/ниже эталона -> переспросить.
# Легитимная прогрессия (+2.5-5кг к рабочим 30-50кг) — это +5-15%,
# далеко не 100%, порог не должен её ловить.
WEIGHT_DEVIATION_MULTIPLIER = 2.0
REPS_DEVIATION_MULTIPLIER = 3.0  # повторы разбросаны сильнее веса в норме (1 vs 30), порог шире


def _find_reference_range(data, exercise_normalized):
    """Возвращает (min_weight, max_weight, min_reps, max_reps) — эталонный
    диапазон для сравнения, или None если эталона нет вообще (ни плана,
    ни истории)."""
    program = prog.load_program()
    for day in program.get("days", {}).values():
        for ex in day["exercises"]:
            candidate_normalized = w.normalize_exercise_name(ex["name"], data.get("exercise_aliases", {}))
            if candidate_normalized == exercise_normalized:
                return (ex["weight_min_kg"], ex["weight_max_kg"], ex["reps_min"], ex["reps_max"])

    history = w.get_history_for_exercise(data, exercise_normalized, limit_sessions=5)
    if history:
        all_sets = [s for session in history for s in session["sets"]]
        weights = [s["weight_kg"] for s in all_sets if s.get("weight_kg") is not None]
        reps = [s["reps"] for s in all_sets if s.get("reps") is not None]
        if weights and reps:
            return (min(weights), max(weights), min(reps), max(reps))

    return None


def check_weight_reps_sanity(data, exercise_raw, weight_kg, reps, aliases=None):
    """Проверяет, не выглядит ли weight_kg/reps подозрительно (вероятная
    опечатка) относительно эталонного диапазона (план программы или
    история). Возвращает dict:
        {"suspicious": False} — всё в пределах нормы ИЛИ эталона нет
            вообще (первая запись — не с чем сравнивать, не блокируем)
        {"suspicious": True, "field": "weight"|"reps", "question": str}
            — явное отклонение, вызывающий код должен переспросить,
            НЕ записывать сразу

    Вес None (упражнение без веса, например подтягивания) не проверяется
    — нечего сравнивать."""
    if aliases is None:
        aliases = {}
    exercise_normalized = w.normalize_exercise_name(exercise_raw, aliases)
    ref = _find_reference_range(data, exercise_normalized)
    if ref is None:
        return {"suspicious": False}

    min_w, max_w, min_r, max_r = ref

    if weight_kg is not None and min_w is not None and max_w is not None:
        if weight_kg > max_w * WEIGHT_DEVIATION_MULTIPLIER or (min_w > 0 and weight_kg < min_w / WEIGHT_DEVIATION_MULTIPLIER):
            return {
                "suspicious": True, "field": "weight",
                "question": (
                    f"Вес {weight_kg}кг сильно отличается от обычного диапазона "
                    f"({min_w}-{max_w}кг) для «{exercise_normalized}». Точно верно? "
                    f"Если да, напиши ещё раз, я запишу."
                ),
            }

    if reps is not None and min_r is not None and max_r is not None:
        if reps > max_r * REPS_DEVIATION_MULTIPLIER or (min_r > 0 and reps < max(1, min_r / REPS_DEVIATION_MULTIPLIER)):
            return {
                "suspicious": True, "field": "reps",
                "question": (
                    f"{reps} повторов сильно отличается от обычного диапазона "
                    f"({min_r}-{max_r}) для «{exercise_normalized}». Точно верно? "
                    f"Если да, напиши ещё раз, я запишу."
                ),
            }

    return {"suspicious": False}
