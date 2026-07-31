"""Единая метрика прогресса — сводит три компонента в одно число 0-100,
идея вдохновлена индустриальными едиными показателями (Jefit's NSPI —
объём+баланс+сила+постоянство в одном числе), но построена прозрачно
и объяснимо на СВОИХ данных, не копирует чужой проприетарный алгоритм.

Три компонента (согласовано с Антоном 31.07.2026):
1. Объём — суммарный тоннаж за период против предыдущего периода той
   же длины (например, эта неделя против прошлой).
2. Сила — средний тренд оценки 1RM (формула Epley, strength.py) по
   всем упражнениям, у которых есть минимум 2 сессии в истории.
3. Постоянство — % реально выполненных тренировок из запланированных
   по расписанию за период (явные и тихие пропуски снижают показатель
   одинаково — оба означают 'тренировки не было').

Итоговый индекс — среднее трёх компонентов (равный вес, не выдумываем
разную важность без основания). Каждый компонент возвращается
отдельно в отчёте — не скрываем, из чего складывается число, это
прозрачность, не чёрный ящик."""
from datetime import datetime, timedelta, timezone

import program as prog
import strength
import workouts as w


def compute_volume_component(data, days, now=None):
    """Объём: тоннаж текущего периода против предыдущего периода той
    же длины. Возвращает dict {"score": 0-100, "current_tonnage",
    "prior_tonnage", "change_pct" или None}. score=50 (нейтрально),
    если нет данных за предыдущий период — не с чем сравнивать, не
    штрафуем и не поощряем на отсутствии истории."""
    if now is None:
        now = datetime.now(timezone.utc)

    current_start = (now - timedelta(days=days - 1)).date()
    current_end = now.date()
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)

    current_dates = {(current_start + timedelta(days=i)).isoformat() for i in range((current_end - current_start).days + 1)}
    prior_dates = {(prior_start + timedelta(days=i)).isoformat() for i in range((prior_end - prior_start).days + 1)}

    current_tonnage = sum(
        (s.get("weight_kg") or 0) * s.get("reps", 0)
        for s in data.get("sets", []) if s["date"] in current_dates and w.is_countable_for_tonnage(s)
    )
    prior_tonnage = sum(
        (s.get("weight_kg") or 0) * s.get("reps", 0)
        for s in data.get("sets", []) if s["date"] in prior_dates and w.is_countable_for_tonnage(s)
    )

    if prior_tonnage == 0:
        return {"score": 50, "current_tonnage": round(current_tonnage), "prior_tonnage": 0, "change_pct": None}

    change_pct = round((current_tonnage - prior_tonnage) / prior_tonnage * 100)
    # Скор: 50 = без изменений, +1 очко за каждый +1% (капается 0-100) —
    # простое, объяснимое отображение процента в шкалу 0-100, не скрытая математика
    score = max(0, min(100, 50 + change_pct))
    return {
        "score": score,
        "current_tonnage": round(current_tonnage),
        "prior_tonnage": round(prior_tonnage),
        "change_pct": change_pct,
    }


def compute_strength_component(data):
    """Сила: средний тренд оценки 1RM по всем упражнениям с >= 2
    сессиями в истории. Возвращает dict {"score": 0-100,
    "exercises_tracked": int, "avg_trend_pct": float или None}.
    score=50 (нейтрально), если нет ни одного упражнения с достаточной
    историей — не с чем считать тренд."""
    exercises = w.known_exercises(data)
    trends = []

    for exercise in exercises:
        history = w.get_history_for_exercise(data, exercise, limit_sessions=2)
        if len(history) < 2:
            continue

        first_best = None
        last_best = None
        for s in history[0]["sets"]:
            if not w.is_countable_for_tonnage(s):
                continue
            est = strength.estimate_1rm(s.get("weight_kg"), s.get("reps"))
            if est is not None and (first_best is None or est > first_best):
                first_best = est
        for s in history[-1]["sets"]:
            if not w.is_countable_for_tonnage(s):
                continue
            est = strength.estimate_1rm(s.get("weight_kg"), s.get("reps"))
            if est is not None and (last_best is None or est > last_best):
                last_best = est

        if first_best and last_best and first_best > 0:
            trends.append((last_best - first_best) / first_best * 100)

    if not trends:
        return {"score": 50, "exercises_tracked": 0, "avg_trend_pct": None}

    avg_trend_pct = round(sum(trends) / len(trends), 1)
    score = max(0, min(100, round(50 + avg_trend_pct * 2)))  # x2: рост силы обычно медленнее объёма, усиливаем чувствительность шкалы
    return {"score": score, "exercises_tracked": len(trends), "avg_trend_pct": avg_trend_pct}


def compute_consistency_component(data, days, now=None):
    """Постоянство: % реально выполненных тренировочных дней из
    запланированных по расписанию за период. Явный (mark_day_skipped)
    и тихий (session.check_and_mark_silent_skip) пропуски оба считаются
    'не выполнено' — с точки зрения метрики постоянства не важно, была
    ли причина уважительной, важен факт. Возвращает dict {"score":
    0-100, "completed": int, "scheduled": int}. score=100 (не 50!),
    если по расписанию не было ни одного тренировочного дня за период
    — нечего было выполнять, не наказываем за отсутствие требований."""
    if now is None:
        now = datetime.now(timezone.utc)

    period_start = (now - timedelta(days=days - 1)).date()
    scheduled_dates = []
    for i in range(days):
        candidate = period_start + timedelta(days=i)
        if prog.WEEKDAY_TO_DAY_ID.get(candidate.weekday()) is not None:
            scheduled_dates.append(candidate.isoformat())

    if not scheduled_dates:
        return {"score": 100, "completed": 0, "scheduled": 0}

    trained_dates = {s["date"] for s in data.get("sets", [])}
    completed = sum(1 for d in scheduled_dates if d in trained_dates)
    score = round(completed / len(scheduled_dates) * 100)
    return {"score": score, "completed": completed, "scheduled": len(scheduled_dates)}


def compute_progress_index(data, days=7, now=None):
    """Считает все три компонента и итоговый индекс (равновзвешенное
    среднее). Возвращает dict {"index": int, "volume": {...},
    "strength": {...}, "consistency": {...}}."""
    volume = compute_volume_component(data, days, now=now)
    strength_comp = compute_strength_component(data)
    consistency = compute_consistency_component(data, days, now=now)

    index = round((volume["score"] + strength_comp["score"] + consistency["score"]) / 3)
    return {
        "index": index,
        "volume": volume,
        "strength": strength_comp,
        "consistency": consistency,
    }


def format_progress_index_report(data, days=7, now=None):
    """Текстовый отчёт с итоговым индексом и разбивкой по компонентам
    — прозрачность, не чёрный ящик: видно, из чего складывается число."""
    result = compute_progress_index(data, days, now=now)
    period_name = "неделю" if days == 7 else "месяц" if days == 30 else f"{days} дн."

    emoji = "\U0001f7e2" if result["index"] >= 70 else "\U0001f7e1" if result["index"] >= 40 else "\U0001f534"
    lines = [f"{emoji} <b>Индекс прогресса за {period_name}: {result['index']}/100</b>\n"]

    vol = result["volume"]
    if vol["change_pct"] is not None:
        sign = "+" if vol["change_pct"] >= 0 else ""
        lines.append(f"\U0001f4ca Объём: {vol['score']}/100 (тоннаж {sign}{vol['change_pct']}% к прошлому периоду)")
    else:
        lines.append(f"\U0001f4ca Объём: {vol['score']}/100 (нет данных за прошлый период для сравнения)")

    strength_comp = result["strength"]
    if strength_comp["avg_trend_pct"] is not None:
        sign = "+" if strength_comp["avg_trend_pct"] >= 0 else ""
        lines.append(
            f"\U0001f3cb Сила: {strength_comp['score']}/100 "
            f"(средний тренд 1RM {sign}{strength_comp['avg_trend_pct']}% по {strength_comp['exercises_tracked']} упр.)"
        )
    else:
        lines.append(f"\U0001f3cb Сила: {strength_comp['score']}/100 (недостаточно истории для тренда)")

    cons = result["consistency"]
    lines.append(f"\U0001f4c5 Постоянство: {cons['score']}/100 ({cons['completed']}/{cons['scheduled']} тренировок по расписанию)")

    return "\n".join(lines)
