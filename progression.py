"""Логика предложений прогрессии — когда предложить увеличить вес/повторы
на следующую тренировку.

ПРИНЦИП (согласован с Антоном 28.07.2026): бот ПРЕДЛАГАЕТ, не меняет
программу автоматически. Каждое предложение уходит в Telegram с кнопками
👍/👎 (тот же паттерн, что filings.py) — target в workouts.json меняется
только при подтверждении, никогда молча.

Алгоритм — double progression, стандартный и объяснимый подход
(не "AI решает по наитию"): у каждого упражнения есть целевой диапазон
повторов (по умолчанию 8-12, если не задан иначе). Пока текущий вес не
даёт дожать верхнюю границу диапазона на всех подходах — повторы растут.
Как только все подходы стабильно на верхней границе БЕЗ ухудшения
(RPE<=8, нет note про тяжело/на грани) — предлагаем шаг веса вверх и
сброс повторов к низу диапазона.

Safety проверяется ПЕРВЫМ, до всей остальной логики: и hard_block, и
manual_progression_only полностью блокируют автоматическое предложение
— по правилу safety_constraints.json manual_progression_only означает
"прогрессия НЕ предлагается автоматически, только по явному запросу",
это не другая формулировка того же предложения, это отсутствие
предложения через этот механизм вообще.
"""
import safety
import workouts as w

DEFAULT_REP_RANGE = (8, 12)
MIN_SESSIONS_FOR_SUGGESTION = 2  # меньше — не с чем сравнивать тренд
WEIGHT_STEP_KG = 2.5             # стандартный шаг для гантелей/блинов в большинстве залов
HIGH_RPE_THRESHOLD = 9           # RPE >= это — не предлагаем прогрессию, тело сигналит "хватит"
HARD_NOTE_KEYWORDS = ["тяжело", "на грани", "с трудом", "еле", "через силу"]
LOW_SLEEP_THRESHOLD_HOURS = 6    # сон < это — прогрессия не предлагается на основе этой тренировки
HIGH_STRESS_THRESHOLD = 7        # стресс >= это (шкала 1-10) — прогрессия не предлагается


def _session_all_at_top_of_range(session_sets, rep_range):
    """True, если ВСЕ сеты сессии достигли верхней границы диапазона
    повторов (или больше) — сигнал, что пора расти в весе, не в повторах."""
    top = rep_range[1]
    return all(s.get("reps", 0) >= top for s in session_sets)


def _session_shows_difficulty(session_sets):
    """True, если хоть один сет сессии показывает признак трудности:
    высокий RPE или note с ключевым словом сложности. Наличие ЛЮБОГО
    такого сигнала блокирует предложение роста веса — это не "средний
    RPE по сессии", один тяжёлый подход достаточен для осторожности."""
    for s in session_sets:
        rpe = s.get("rpe")
        if rpe is not None and rpe >= HIGH_RPE_THRESHOLD:
            return True
        note = (s.get("note") or "").lower()
        if any(kw in note for kw in HARD_NOTE_KEYWORDS):
            return True
    return False


def _session_wellness_bad(data, session_date):
    """True, если для даты сессии есть запись в wellness_log И (сон <
    LOW_SLEEP_THRESHOLD_HOURS ИЛИ стресс >= HIGH_STRESS_THRESHOLD).
    Отсутствие записи о самочувствии (тренировка была до этой фичи, или
    Антон не заполнял дневник) НЕ блокирует прогрессию — блокирует
    только ПОДТВЕРЖДЁННОЕ плохое самочувствие, не его отсутствие."""
    wellness = w.get_wellness_for_date(data, session_date)
    if wellness is None:
        return False
    sleep_hours = wellness.get("sleep_hours")
    stress_level = wellness.get("stress_level")
    if sleep_hours is not None and sleep_hours < LOW_SLEEP_THRESHOLD_HOURS:
        return True
    if stress_level is not None and stress_level >= HIGH_STRESS_THRESHOLD:
        return True
    return False


def suggest_progression(data, exercise, rep_range=None):
    """Возвращает предложение прогрессии для exercise (нормализованное
    имя) или None, если предлагать нечего.

    Возврат — dict {"action": "increase_weight"|"increase_reps"|"hold",
    "reasoning": str, "suggested_weight_kg": float, "suggested_reps": int}
    или None (недостаточно данных, safety-блок, или сейчас рано —
    None означает "бот молчит по этому упражнению в этот раз", не ошибку).
    """
    safety_result = safety.check_exercise(exercise)
    if safety_result["status"] in ("hard_block", "manual_progression_only"):
        # По правилу safety_constraints.json: manual_progression_only значит
        # "прогрессия НЕ предлагается автоматически — только по явному
        # запросу Антона". Это не другая формулировка того же предложения,
        # это отсутствие предложения вообще через этот механизм.
        return None

    if rep_range is None:
        rep_range = DEFAULT_REP_RANGE

    history = w.get_history_for_exercise(data, exercise, limit_sessions=MIN_SESSIONS_FOR_SUGGESTION)
    if len(history) < MIN_SESSIONS_FOR_SUGGESTION:
        return None  # недостаточно истории, рано что-то предлагать

    recent_sessions = history[-MIN_SESSIONS_FOR_SUGGESTION:]

    # Если хоть одна из последних сессий показывает трудность — не растим,
    # держим текущий вес/повторы. Это осторожная сторона ошибки намеренно:
    # ложный "рано расти" безвреден, ложный "пора расти" при реальной
    # усталости — риск травмы или перетренированности.
    if any(_session_shows_difficulty(sess["sets"]) for sess in recent_sessions):
        return None

    # Тот же принцип для самочувствия (согласовано с Антоном 28.07.2026):
    # плохой сон (<6ч) ИЛИ высокий стресс (>=7) в ЛЮБОЙ из последних
    # сессий блокирует прогрессию — даже если подходы формально были
    # чистые (пошаговый флоу всегда пишет план-максимум, см. bot.py, не
    # то, что реально удалось "через силу"). Отсутствие данных о
    # самочувствии НЕ блокирует — блокирует только подтверждённое плохое.
    if any(_session_wellness_bad(data, sess["date"]) for sess in recent_sessions):
        return None

    all_at_top = all(_session_all_at_top_of_range(sess["sets"], rep_range) for sess in recent_sessions)

    last_session = recent_sessions[-1]["sets"]
    last_weight = last_session[-1].get("weight_kg") if last_session else None

    if all_at_top:
        if last_weight is None:
            return None  # упражнение без веса (например, подтягивания без отягощения) — вес не прогрессируем этим путём
        new_weight = round(last_weight + WEIGHT_STEP_KG, 1)
        reasoning = (
            f"Последние {MIN_SESSIONS_FOR_SUGGESTION} тренировки все подходы на {rep_range[1]}+ "
            f"повторов без признаков трудности — можно поднять вес."
        )
        return {
            "action": "increase_weight",
            "reasoning": reasoning,
            "suggested_weight_kg": new_weight,
            "suggested_reps": rep_range[0],
        }

    return None  # ещё есть куда расти в повторах на текущем весе — прогрессия по весу пока рано


def format_suggestion_message(exercise, suggestion):
    """Готовый текст для отправки в Telegram."""
    return (
        f"💪 <b>{exercise}</b>\n"
        f"{suggestion['reasoning']}\n\n"
        f"Предлагаю: <b>{suggestion['suggested_weight_kg']}кг × {suggestion['suggested_reps']}</b> "
        f"на следующей тренировке."
    )
