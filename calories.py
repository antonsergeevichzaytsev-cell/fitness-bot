"""Оценка калорий за тренировку по MET-формуле — стандартный метод
(Compendium of Physical Activities), точность ±10-20%, не претендует
на точный расчёт (силовую тренировку в принципе нельзя посчитать точно
без метаболической камеры — это осознанная оценка, не измерение).

Формула: calories = duration_minutes × (MET × 3.5 × weight_kg) / 200
MET = 3.5 для силовой тренировки умеренной интенсивности (стандартное
значение из Compendium of Physical Activities, используется большинством
калькуляторов калорий для resistance training).
"""
from datetime import datetime, timezone

MET_RESISTANCE_TRAINING = 3.5


def estimate_calories(weight_kg, duration_minutes, met=MET_RESISTANCE_TRAINING):
    """calories = duration_min × (MET × 3.5 × weight_kg) / 200.

    Возвращает None, если weight_kg или duration_minutes не заданы
    (None или <= 0) — не выдаём число на пустых/некорректных входных
    данных, честное 'не посчитано' лучше правдоподобной, но выдуманной
    цифры (тот же принцип, что в parser.py — не угадывать)."""
    if not weight_kg or weight_kg <= 0:
        return None
    if not duration_minutes or duration_minutes <= 0:
        return None
    return round(duration_minutes * (met * 3.5 * weight_kg) / 200)


def session_duration_minutes(started_ts, ended_ts=None):
    """Продолжительность тренировки в минутах — started_ts (ISO строка)
    до ended_ts (ISO строка, по умолчанию сейчас). Возвращает None при
    некорректном/отсутствующем started_ts, не бросает исключение наружу
    (вызывающий код в bot.py не должен падать на битых данных сессии)."""
    if not started_ts:
        return None
    try:
        start = datetime.fromisoformat(started_ts)
    except (ValueError, TypeError):
        return None
    end = datetime.fromisoformat(ended_ts) if ended_ts else datetime.now(timezone.utc)
    return round((end - start).total_seconds() / 60, 1)
