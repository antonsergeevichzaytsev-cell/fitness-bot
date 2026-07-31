"""Расширенная оценка готовности к тренировке — сводит несколько
имеющихся сигналов (сон, стресс, недавние RPE, тренд тренировок,
пропуски) в единую детерминированную оценку 0-100, затем DeepSeek
формулирует объяснение на естественном языке.

ЧЕСТНОЕ ОГРАНИЧЕНИЕ (не скрывать от пользователя, явно называть в
тексте): это НЕ то же самое, что HRV/сон с реального носимого
устройства (Whoop, Garmin, Oura) — все сигналы введены вручную текстом
или получены из истории тренировок в этом же боте, не объективные
физиологические измерения. Это лучшая доступная оценка на имеющихся
данных, не замена настоящей биометрии.

Архитектурный принцип (тот же, что в safety.py/sanity.py по всему
репозиторию): DECISION делает ДЕТЕРМИНИРОВАННЫЙ КОД (compute_readiness_
score), НЕ LLM. DeepSeek вызывается ТОЛЬКО для explain_readiness —
формулирует объяснение уже принятого решения человеческим языком,
не принимает решение сам. Если DeepSeek недоступен — числовая оценка
и её базовое текстовое обоснование всё равно работают (graceful
degradation, не завязано намертво на сеть)."""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import net
import workouts as w
from parser import DEEPSEEK_KEY, DEEPSEEK_URL

# Пороги для детерминированной оценки — подобраны по здравому смыслу,
# не выдуманы под конкретный случай. Каждый сигнал вносит вклад в
# итоговый счёт 0-100 (100 = полностью готов), НЕ умножается друг на
# друга (аддитивная модель, не мультипликативная) — так проще объяснить
# и предсказуемее вести себя на граничных случаях.
BASE_SCORE = 100
LOW_SLEEP_PENALTY = 20          # сон < 6ч в последней записи
HIGH_STRESS_PENALTY = 20        # стресс >= 7 в последней записи
NO_WELLNESS_DATA_PENALTY = 0    # отсутствие данных НЕ штрафуется — нечестно наказывать за то, чего не вводили
HIGH_RPE_PENALTY = 15           # RPE >= 9 в любом сете за последние RPE_LOOKBACK_SETS
RECENT_SKIP_PENALTY = 10        # пропуск (явный или тихий) в последние SKIP_LOOKBACK_DAYS дней
DECLINING_TONNAGE_PENALTY = 15  # тоннаж последней сессии ниже предыдущей более чем на 10%

RPE_LOOKBACK_SETS = 10
SKIP_LOOKBACK_DAYS = 14
HIGH_RPE_THRESHOLD = 9
LOW_SLEEP_THRESHOLD_HOURS = 6
HIGH_STRESS_THRESHOLD = 7
TONNAGE_DECLINE_THRESHOLD_PCT = 10


def collect_signals(data, exercise_for_trend=None):
    """Детерминированно собирает доступные сигналы готовности из
    существующих хранилищ (wellness_log, sets, skipped_days). Не
    обращается к сети, не использует LLM — чистая агрегация уже
    записанных данных.

    exercise_for_trend — если задано, добавляет тренд тоннажа именно
    по этому упражнению (для контекста конкретной тренировки); если
    None, тренд не считается (сигнал остаётся None)."""
    signals = {
        "sleep_hours": None,
        "stress_level": None,
        "wellness_date": None,
        "recent_high_rpe_count": 0,
        "recent_sets_checked": 0,
        "recent_skip_within_days": None,
        "tonnage_trend_pct": None,
    }

    wellness_history = sorted(data.get("wellness_log", {}).items())
    if wellness_history:
        last_date, last_wellness = wellness_history[-1]
        signals["sleep_hours"] = last_wellness.get("sleep_hours")
        signals["stress_level"] = last_wellness.get("stress_level")
        signals["wellness_date"] = last_date

    recent_sets = sorted(data.get("sets", []), key=lambda s: s["ts"])[-RPE_LOOKBACK_SETS:]
    signals["recent_sets_checked"] = len(recent_sets)
    signals["recent_high_rpe_count"] = sum(
        1 for s in recent_sets if s.get("rpe") is not None and s["rpe"] >= HIGH_RPE_THRESHOLD
    )

    now = datetime.now(timezone.utc)
    for days_back in range(1, SKIP_LOOKBACK_DAYS + 1):
        check_date = (now - timedelta(days=days_back)).date().isoformat()
        if w.is_day_skipped(data, check_date):
            signals["recent_skip_within_days"] = days_back
            break

    if exercise_for_trend:
        history = w.get_history_for_exercise(data, exercise_for_trend, limit_sessions=2)
        if len(history) == 2:
            prior_tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in history[0]["sets"])
            last_tonnage = sum((s.get("weight_kg") or 0) * s.get("reps", 0) for s in history[1]["sets"])
            if prior_tonnage > 0:
                signals["tonnage_trend_pct"] = round((last_tonnage - prior_tonnage) / prior_tonnage * 100)

    return signals


def compute_readiness_score(signals):
    """Детерминированная оценка 0-100 по аддитивной модели штрафов.
    Возвращает dict {"score": int, "factors": [str, ...]} — factors
    перечисляет ИМЕННО ТЕ штрафы, которые реально применились (не
    пустой список формальностей), нужен для честного объяснения,
    какие сигналы реально повлияли на итог."""
    score = BASE_SCORE
    factors = []

    if signals["sleep_hours"] is not None and signals["sleep_hours"] < LOW_SLEEP_THRESHOLD_HOURS:
        score -= LOW_SLEEP_PENALTY
        factors.append(f"сон {signals['sleep_hours']}ч (ниже {LOW_SLEEP_THRESHOLD_HOURS}ч)")

    if signals["stress_level"] is not None and signals["stress_level"] >= HIGH_STRESS_THRESHOLD:
        score -= HIGH_STRESS_PENALTY
        factors.append(f"стресс {signals['stress_level']}/10 (выше {HIGH_STRESS_THRESHOLD})")

    if signals["recent_high_rpe_count"] > 0:
        score -= HIGH_RPE_PENALTY
        factors.append(f"{signals['recent_high_rpe_count']} подход(ов) с высоким RPE из последних {signals['recent_sets_checked']}")

    if signals["recent_skip_within_days"] is not None:
        score -= RECENT_SKIP_PENALTY
        factors.append(f"был пропуск тренировки {signals['recent_skip_within_days']} дн. назад")

    if signals["tonnage_trend_pct"] is not None and signals["tonnage_trend_pct"] < -TONNAGE_DECLINE_THRESHOLD_PCT:
        score -= DECLINING_TONNAGE_PENALTY
        factors.append(f"тоннаж последней тренировки упал на {abs(signals['tonnage_trend_pct'])}%")

    score = max(0, min(100, score))
    return {"score": score, "factors": factors}


def explain_readiness(signals, result):
    """Вызывает DeepSeek, чтобы сформулировать УЖЕ ПРИНЯТОЕ решение
    (score + factors из compute_readiness_score) человеческим языком.
    DeepSeek НЕ пересчитывает оценку и не может её изменить — только
    объясняет. Если DeepSeek недоступен — возвращает базовое текстовое
    объяснение без LLM (graceful degradation), не блокирует функцию
    целиком отсутствием сети."""
    if not DEEPSEEK_KEY:
        return _fallback_explanation(result)

    factors_text = "; ".join(result["factors"]) if result["factors"] else "нет отрицательных сигналов"
    user_msg = (
        f"Оценка готовности к тренировке: {result['score']}/100. "
        f"Факторы, повлиявшие на оценку: {factors_text}. "
        f"Напиши короткое (2-3 предложения) объяснение на русском, почему такая оценка, "
        f"и практичный совет (тренироваться как обычно / снизить интенсивность / отдохнуть)."
    )
    system_prompt = (
        "Ты объясняешь пользователю УЖЕ ПОСЧИТАННУЮ оценку готовности к тренировке "
        "(0-100, посчитана кодом, не тобой). Не меняй оценку и не придумывай новые факторы — "
        "используй только те, что даны. Пиши кратко, по-человечески, без канцелярита."
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.4,
        "max_tokens": 250,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=data,
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
    )
    try:
        with net.urlopen_retry(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ! readiness explanation error, falling back: {e}", file=sys.stderr)
        return _fallback_explanation(result)


def _fallback_explanation(result):
    """Базовое объяснение без LLM — используется, если DeepSeek
    недоступен или DEEPSEEK_API_KEY не настроен. Не такое живое, как
    LLM-версия, но функция не должна ломаться из-за отсутствия сети."""
    if not result["factors"]:
        return "Явных отрицательных сигналов нет — можно тренироваться как обычно."
    factors_text = "; ".join(result["factors"])
    if result["score"] >= 70:
        advice = "можно тренироваться как обычно, но обрати внимание на самочувствие в процессе."
    elif result["score"] >= 40:
        advice = "стоит снизить интенсивность сегодня — меньше веса или больше отдыха между подходами."
    else:
        advice = "лучше отдохнуть или сделать лёгкую тренировку — риск перегрузки высок."
    return f"Факторы: {factors_text}. Совет: {advice}"


def format_readiness_report(data, exercise_for_trend=None):
    """Собирает сигналы, считает оценку, формулирует объяснение —
    полный цикл в одну функцию для вызова из bot.py. Всегда начинается
    с честного дисклеймера про отсутствие реальных биометрических
    данных (сон/стресс введены вручную, не с устройства)."""
    signals = collect_signals(data, exercise_for_trend=exercise_for_trend)
    result = compute_readiness_score(signals)
    explanation = explain_readiness(signals, result)

    emoji = "\U0001f7e2" if result["score"] >= 70 else "\U0001f7e1" if result["score"] >= 40 else "\U0001f534"
    return (
        f"{emoji} <b>Готовность: {result['score']}/100</b>\n"
        f"<i>(оценка по введённым тобой данным — не с носимого устройства, не HRV)</i>\n\n"
        f"{explanation}"
    )
