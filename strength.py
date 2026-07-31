"""Оценка максимального веса на один повтор (1RM) по формуле Epley —
индустриальный стандарт (используется большинством топовых приложений,
Hevy/Strong/Jefit все реализуют её как минимум).

Формула Epley: 1RM = вес × (1 + повторы/30). Наиболее точна при 1-10
повторах, выше — точность резко падает (усталость и техника начинают
искажать оценку сильнее, чем сама формула может учесть). Честно
предупреждаем об этом в выводе, не выдаём оценку по 15+ повторам как
надёжное число.
"""
import workouts as w

EPLEY_REP_DIVISOR = 30
HIGH_REP_ACCURACY_THRESHOLD = 10  # выше — точность оценки резко падает, предупреждаем явно


def estimate_1rm(weight_kg, reps):
    """1RM по формуле Epley. Возвращает None, если weight_kg или reps
    не заданы/некорректны (0 или отрицательные) — не считаем оценку
    на бессмысленных входных данных."""
    if not weight_kg or weight_kg <= 0:
        return None
    if not reps or reps <= 0:
        return None
    return round(weight_kg * (1 + reps / EPLEY_REP_DIVISOR), 1)


def find_best_set_for_1rm(data, exercise, limit_sessions=10):
    """Ищет сет с максимальной оценкой 1RM среди последних limit_sessions
    тренировок этого упражнения — не просто 'максимальный вес' (тяжёлый
    подход на 1 повтор и лёгкий на 8 повторов могут давать разную
    оценку истинного 1RM, Epley учитывает это). Разминочные подходы
    (set_type='warmup') исключаются — они не отражают рабочую силу.

    Возвращает dict {"weight_kg", "reps", "date", "estimated_1rm"} или
    None, если истории по упражнению нет вообще."""
    history = w.get_history_for_exercise(data, exercise, limit_sessions=limit_sessions)
    if not history:
        return None

    best = None
    for session in history:
        for s in session["sets"]:
            if not w.is_countable_for_tonnage(s):
                continue
            estimate = estimate_1rm(s.get("weight_kg"), s.get("reps"))
            if estimate is None:
                continue
            if best is None or estimate > best["estimated_1rm"]:
                best = {
                    "weight_kg": s["weight_kg"],
                    "reps": s["reps"],
                    "date": session["date"],
                    "estimated_1rm": estimate,
                }
    return best


def format_1rm_report(data, exercise):
    """Строит текстовый отчёт с оценкой 1RM по лучшему подходу из
    истории. Возвращает None, если истории нет (вызывающий код должен
    явно обработать этот случай — не показываем пустой отчёт)."""
    best = find_best_set_for_1rm(data, exercise)
    if best is None:
        return None

    lines = [
        f"\U0001f3cb <b>Оценка 1RM: {exercise}</b>\n",
        f"По подходу {best['weight_kg']}кг \u00d7 {best['reps']} ({best['date']}):",
        f"<b>~{best['estimated_1rm']} кг</b> (формула Epley)",
    ]

    if best["reps"] > HIGH_REP_ACCURACY_THRESHOLD:
        lines.append(
            f"\n\u26a0\ufe0f Подход был на {best['reps']} повторов — оценка на высоких "
            f"повторах менее точна (формула надёжнее при 1-{HIGH_REP_ACCURACY_THRESHOLD} повторах), "
            f"относись к числу как к грубому ориентиру, не точному максимуму."
        )

    return "\n".join(lines)
