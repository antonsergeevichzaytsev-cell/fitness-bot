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
from datetime import datetime, timezone

import workouts as w

START_KEYWORDS = ["начал", "начинаю", "старт", "погнали", "поехали тренир"]
END_KEYWORDS = ["закончил", "конец тренировки", "финиш", "всё, закончили", "готово с тренировкой"]


def is_session_start(text):
    t = text.strip().lower()
    return any(kw in t for kw in START_KEYWORDS)


def is_session_end(text):
    t = text.strip().lower()
    return any(kw in t for kw in END_KEYWORDS)


def start_session(data):
    """Открывает сессию — запоминает момент старта и дату. Идемпотентно:
    повторный вызов при уже открытой сессии не создаёт вторую, просто
    ничего не делает (защита от случайного повторного 'начал')."""
    if data.get("active_session"):
        return False  # уже открыта
    data["active_session"] = {
        "started_ts": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).date().isoformat(),
    }
    return True


def end_session(data):
    """Закрывает сессию, возвращает (exercises_today, session_date) для
    построения отчёта, либо (None, None) если сессия не была открыта —
    вызывающий код должен явно обработать этот случай, не молча
    проигнорировать."""
    session = data.get("active_session")
    if not session:
        return None, None

    session_date = session["date"]
    today_sets = [s for s in data.get("sets", []) if s["date"] == session_date]
    exercises_today = sorted({s["exercise"] for s in today_sets})

    data["active_session"] = None
    return exercises_today, session_date


def is_session_active(data):
    return bool(data.get("active_session"))


def build_session_report(data, exercises, session_date):
    """Строит текст отчёта: по каждому упражнению сессии — сравнение с
    предыдущей тренировкой этого упражнения (тоннаж туда-сюда), если
    такая история есть."""
    if not exercises:
        return "Тренировка завершена, но ни одного подхода не записано."

    lines = ["\U0001f3c1 <b>Тренировка завершена</b>\n"]

    for exercise in exercises:
        history = w.get_history_for_exercise(data, exercise, limit_sessions=5)
        today_sessions = [h for h in history if h["date"] == session_date]
        if not today_sessions:
            continue
        today_sets = today_sessions[0]["sets"]

        prior_sessions = [h for h in history if h["date"] < session_date]
        summary = _format_exercise_today(exercise, today_sets)
        if prior_sessions:
            trend = _format_trend(today_sets, prior_sessions[-1]["sets"])
            summary += f"\n  {trend}"
        lines.append(summary)

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
