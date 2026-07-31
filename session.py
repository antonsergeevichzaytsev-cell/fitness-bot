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
import re
from datetime import datetime, timedelta, timezone

import calories as cal
import program as prog
import workouts as w

START_KEYWORDS = ["начал", "начинаю", "старт", "погнали", "поехали тренир"]
END_KEYWORDS = ["закончил", "конец тренировки", "финиш", "всё, закончили", "готово с тренировкой"]
SET_DONE_KEYWORDS = ["взял", "готово", "сделал", "есть"]
EXTEND_REST_KEYWORDS = ["продли", "ещё минут", "ещё секунд", "устал", "нужно больше", "добавь врем"]
REPLACE_KEYWORDS = ["замени", "заменить", "занят", "не работает", "сломан", "другое упражнение"]
SKIP_KEYWORDS = ["пропусти", "пропуск", "не буду делать", "скип"]
SKIP_DAY_KEYWORDS = ["пропуск дня", "болею", "заболел", "нет тренировкам", "не будет тренировки",
                     "сегодня не тренируюсь", "пропускаю сегодня"]
UNDO_KEYWORDS = ["отмени", "отмена", "убери последн", "не то записал", "ошибся"]
PROGRESS_KEYWORDS = ["покажи прогресс", "прогресс по", "как дела с", "статистика по", "покажи статистику"]
GOAL_KEYWORDS = ["цель по весу", "прогресс по весу", "покажи цель", "сколько до цели", "динамика веса"]
READINESS_KEYWORDS = ["готовность", "как я готов", "готов ли я", "оцени готовность"]
ONE_RM_KEYWORDS = ["1rm", "1рм", "макс на раз", "максимум на один повтор", "мой максимум"]
WEEK_SUMMARY_KEYWORDS = ["итоги недели", "итоги за неделю", "сводка за неделю", "статистика за неделю"]
MONTH_SUMMARY_KEYWORDS = ["итоги месяца", "итоги за месяц", "сводка за месяц", "статистика за месяц"]
CARDIO_KEYWORD = "кардио"
PHASE_KEYWORD = "фаза"
PHASE_NAME_TO_ID = {"силовой": "strength", "силовая": "strength",
                     "объёмный": "volume", "объемный": "volume", "объёмная": "volume", "объемная": "volume",
                     "дефицитный": "deficit", "дефицитная": "deficit", "дефицит": "deficit"}


def is_session_start(text):
    t = text.strip().lower()
    return any(kw in t for kw in START_KEYWORDS)


def is_session_end(text):
    t = text.strip().lower()
    return any(kw in t for kw in END_KEYWORDS)


def is_set_confirmation(text):
    """Короткое подтверждение 'подход сделан' в пошаговом флоу — 'взял',
    'готово', 'сделал', 'есть'. Ограничение по длине (<=4 слова) намеренно:
    без него 'сделал присед 50 на 8, тяжело пошёл' тоже совпало бы (там
    есть слово 'сделал'), но это полноценная запись подхода с деталями,
    не короткое 'продолжай' — их нельзя путать, у них разная обработка."""
    t = text.strip().lower()
    if len(t.split()) > 4:
        return False
    return any(kw in t for kw in SET_DONE_KEYWORDS)


def is_extend_rest_request(text):
    """'продли отдых', 'ещё минуту', 'устал, нужно больше времени' —
    просьба продлить текущий таймер отдыха. Тоже детерминированно, не
    DeepSeek: короткая команда, требующая мгновенной реакции, не
    сложного понимания контекста."""
    t = text.strip().lower()
    return any(kw in t for kw in EXTEND_REST_KEYWORDS)


def parse_weight_kg(text):
    """Извлекает число (вес в кг) из ответа на вопрос 'сколько сейчас
    весишь' — '121', '121.5', '121,5 кг', 'где-то 120'. Детерминированно
    (regex), не DeepSeek: простое число, не требует понимания контекста.
    Возвращает float или None, если число не найдено."""
    match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def extract_extend_seconds(text, default_sec=30):
    """Вытаскивает число секунд из просьбы продлить отдых.
    'продли на 30' -> 30, 'ещё минуту' -> 60, 'ещё 2 минуты' -> 120,
    просто 'устал' (без числа) -> default_sec (разумный дефолт, не
    заставляем формулировать точно, когда и так тяжело)."""
    t = text.strip().lower()

    if "минут" in t:
        match = re.search(r"(\d+)\s*минут", t)
        minutes = int(match.group(1)) if match else 1  # "минуту" без цифры -> 1
        return minutes * 60

    match = re.search(r"(\d+)\s*сек", t)
    if match:
        return int(match.group(1))

    # просто число без единиц ("продли на 45") — считаем секундами,
    # это откликается на короткий отдых 45-90 сек в программе
    match = re.search(r"(\d+)", t)
    if match:
        return int(match.group(1))

    return default_sec


def is_replace_exercise_request(text):
    """'замени упражнение', 'тренажёр занят', 'сломан' — просьба
    предложить альтернативу текущему упражнению. Детерминированное
    распознавание НАМЕРЕНИЯ (не выбор самой замены — тот требует
    понимания паттерна движения, отдельный шаг через DeepSeek + проверку
    safety.py перед тем, как предложить что-либо)."""
    t = text.strip().lower()
    return any(kw in t for kw in REPLACE_KEYWORDS)


def is_skip_day_request(text):
    """'болею', 'нет тренировкам', 'пропуск дня' — сообщение о том, что
    сегодня тренировки не будет ВООБЩЕ (не требует активной сессии, не
    про пропуск одного упражнения). Должна проверяться РАНЬШЕ
    is_skip_request в main() — 'пропуск дня' содержит слово 'пропуск',
    которое само по себе есть в SKIP_KEYWORDS."""
    t = text.strip().lower()
    return any(kw in t for kw in SKIP_DAY_KEYWORDS)


def is_skip_request(text):
    """'пропусти', 'скип' — пропустить текущее упражнение целиком (не
    один подход, всё упражнение), перейти к следующему без записи."""
    t = text.strip().lower()
    return any(kw in t for kw in SKIP_KEYWORDS)


def is_undo_request(text):
    """'отмени', 'ошибся' — откатить последнюю записанную запись."""
    t = text.strip().lower()
    return any(kw in t for kw in UNDO_KEYWORDS)


def is_progress_request(text):
    """'покажи прогресс по жиму', 'как дела с приседом' — запрос истории/
    тренда по конкретному упражнению за несколько тренировок."""
    t = text.strip().lower()
    return any(kw in t for kw in PROGRESS_KEYWORDS)


def is_goal_request(text):
    """'цель по весу', 'сколько до цели', 'динамика веса' — запрос
    отчёта о прогрессе к целевому весу тела (не путать с
    is_progress_request — тот про конкретное УПРАЖНЕНИЕ, этот про
    вес тела). Проверяется раньше is_progress_request в main() —
    иначе более общее 'прогресс' в PROGRESS_KEYWORDS могло бы
    перехватить 'прогресс по весу'."""
    t = text.strip().lower()
    return any(kw in t for kw in GOAL_KEYWORDS)


def is_readiness_request(text):
    """'готовность', 'как я готов', 'оцени готовность' — запрос
    расширенной многосигнальной оценки готовности к тренировке
    (readiness.py). Не пересекается с другими наборами ключевых слов
    (проверено явно)."""
    t = text.strip().lower()
    return any(kw in t for kw in READINESS_KEYWORDS)


def is_one_rm_request(text):
    """'1рм жим', 'мой максимум присед' — запрос оценки 1RM
    (strength.py). Английское '1rm' тоже распознаётся — частый способ
    писать, даже в русскоязычном сообщении."""
    t = text.strip().lower()
    return any(kw in t for kw in ONE_RM_KEYWORDS)


def extract_one_rm_query(text):
    """Извлекает название упражнения из запроса 1RM — то, что осталось
    после отбрасывания ключевой фразы, с зачисткой висящих предлогов
    ('на', 'по' — 'мой максимум на жиме' не должен оставить лишнее
    'на жиме' вместо 'жиме'). Пустая строка, если ничего не осталось."""
    t = text.strip().lower()
    for kw in ONE_RM_KEYWORDS:
        idx = t.find(kw)
        if idx != -1:
            rest = t[idx + len(kw):].strip()
            for prefix in ("по ", "на ", "для "):
                if rest.startswith(prefix):
                    rest = rest[len(prefix):].strip()
                    break
            return rest
    return ""


def is_week_summary_request(text):
    """'итоги недели', 'сводка за неделю' — запрос агрегированной
    статистики за последние 7 дней. Проверяется РАНЬШЕ
    is_month_summary_request и is_progress_request в main() (порядок
    важен, если когда-нибудь появятся пересекающиеся слова)."""
    t = text.strip().lower()
    return any(kw in t for kw in WEEK_SUMMARY_KEYWORDS)


def is_month_summary_request(text):
    """'итоги месяца', 'сводка за месяц' — запрос агрегированной
    статистики за последние 30 дней."""
    t = text.strip().lower()
    return any(kw in t for kw in MONTH_SUMMARY_KEYWORDS)


def is_cardio_message(text):
    """'кардио 5км', 'кардио 5' — отдельная команда записи кардио, не
    привязана к пошаговому флоу тренировки, работает в любой момент."""
    t = text.strip().lower()
    return CARDIO_KEYWORD in t


def extract_cardio_km(text):
    """Извлекает километраж из 'кардио 5км' / 'кардио 5.5' / 'кардио
    5 км'. Возвращает float или None, если число не найдено (текст
    после 'кардио' пустой или не число — вызывающий код должен
    переспросить)."""
    t = text.strip().lower()
    idx = t.find(CARDIO_KEYWORD)
    if idx == -1:
        return None
    rest = t[idx + len(CARDIO_KEYWORD):]
    match = re.search(r"(\d+(?:[.,]\d+)?)", rest)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def is_phase_change_request(text):
    """'фаза силовой', 'переключи на дефицит' — просьба сменить блок
    периодизации. Детерминированное распознавание по ключевому слову
    'фаза' ИЛИ по одному из названий фаз (PHASE_NAME_TO_ID) — 'дефицит'
    само по себе тоже валидная команда, не только 'фаза дефицит'."""
    t = text.strip().lower()
    if PHASE_KEYWORD in t:
        return True
    return any(name in t for name in PHASE_NAME_TO_ID)


def extract_phase_id(text):
    """Извлекает id фазы ('strength'/'volume'/'deficit') из текста
    команды смены фазы. Возвращает None, если ни одно известное
    название фазы не найдено в тексте."""
    t = text.strip().lower()
    for name, phase_id in PHASE_NAME_TO_ID.items():
        if name in t:
            return phase_id
    return None


def extract_progress_query(text):
    """Вытаскивает название упражнения из запроса прогресса — то, что
    осталось после отбрасывания ключевой фразы и висящего предлога
    ('по', 'с', 'со' — 'покажи прогресс' не включает 'по', остаток
    'по жиму' иначе оставил бы лишний предлог перед именем).
    workouts.find_exercise_by_partial_name сам справится с падежным
    окончанием через substring-матчинг в обе стороны. Пустая строка,
    если после ключевой фразы ничего не осталось — вызывающий код
    должен переспросить, какое упражнение."""
    t = text.strip().lower()
    for kw in PROGRESS_KEYWORDS:
        idx = t.find(kw)
        if idx != -1:
            rest = t[idx + len(kw):].strip()
            for prefix in ("по ", "с ", "со "):
                if rest.startswith(prefix):
                    rest = rest[len(prefix):].strip()
                    break
            return rest
    return ""


DAILY_REMINDER_HOUR_MSK = 18  # 18:00 МСК — разумный дефолт, точное время не выбрано (открытый вопрос)
MSK_OFFSET_HOURS = 3


def should_send_daily_reminder(data, now=None):
    """True, если сегодня тренировочный день по расписанию, время уже
    >= DAILY_REMINDER_HOUR_MSK, тренировка сегодня ещё не была начата
    (ни одной записи с сегодняшней датой в data['sets']), и напоминание
    на сегодня ещё не отправлено (data['daily_reminder_sent_date'] !=
    сегодняшняя дата).

    Проверяется по факту записей (data['sets']), не по active_session —
    сессия могла быть уже закрыта ('закончил'), а активной может не
    быть вообще, но тренировка технически состоялась."""
    if now is None:
        now = datetime.now(timezone.utc)
    msk_now = now + timedelta(hours=MSK_OFFSET_HOURS)

    if prog.today_day_id(now) is None:
        return False  # сегодня не тренировочный день
    if msk_now.hour < DAILY_REMINDER_HOUR_MSK:
        return False  # ещё рано

    today_str = msk_now.date().isoformat()
    if data.get("daily_reminder_sent_date") == today_str:
        return False  # уже напомнили сегодня

    already_trained = any(s["date"] == today_str for s in data.get("sets", []))
    if already_trained:
        return False

    if w.is_day_skipped(data, today_str):
        return False  # Антон явно сказал, что сегодня тренировки не будет

    return True


def mark_daily_reminder_sent(data, now=None):
    """Помечает, что напоминание на сегодня (по МСК-дате) отправлено —
    не даёт Cron Trigger'у слать его повторно на следующих прогонах
    этого же дня."""
    if now is None:
        now = datetime.now(timezone.utc)
    msk_now = now + timedelta(hours=MSK_OFFSET_HOURS)
    data["daily_reminder_sent_date"] = msk_now.date().isoformat()


def check_and_mark_silent_skip(data, now=None):
    """Проверяет ПРЕДЫДУЩИЙ тренировочный день по расписанию (program.
    previous_training_date) — если в этот день не было ни одной
    записанной тренировки И он ещё не помечен явной командой ('болею'
    через mark_day_skipped), помечает его автоматически как 'тихий'
    пропуск (reason='' — отличает от явного 'болею' с текстом причины,
    но всё ещё is_day_skipped=True).

    НИКОГДА не трогает сегодняшний день — только строго предыдущий
    тренировочный, раз сегодня ещё продолжается и рано делать выводы.

    Возвращает True, если что-то реально пометил (для логирования в
    timer.py — не для пользовательского сообщения, тихая фиксация без
    уведомления, только для полноты данных под будущую сводку)."""
    if now is None:
        now = datetime.now(timezone.utc)
    prev_date = prog.previous_training_date(now)
    if prev_date is None:
        return False

    if w.is_day_skipped(data, prev_date):
        return False  # уже помечен явно или уже помечен тихо ранее

    trained = any(s["date"] == prev_date for s in data.get("sets", []))
    if trained:
        return False  # была тренировка — нечего помечать

    w.mark_day_skipped(data, prev_date, reason="")
    return True


PHASE_REMINDER_WEEKS = 6  # середина диапазона 4-8 недель, согласовано с Антоном 28.07.2026


def should_send_phase_reminder(data, now=None):
    """True, если активный блок периодизации длится >= PHASE_REMINDER_
    WEEKS недель И напоминание об этом ещё не отправлено. False, если
    started_date не задан (фаза 'volume' по умолчанию, никогда не
    менялась явно — нечего отсчитывать) или напоминание уже отправлено
    для текущего блока (reminder_sent сбрасывается только при явной
    смене фазы через set_active_phase, не сам по себе со временем)."""
    if now is None:
        now = datetime.now(timezone.utc)
    phase = w.get_active_phase(data)
    started_date = phase.get("started_date")
    if started_date is None:
        return False
    if phase.get("reminder_sent"):
        return False

    started = datetime.fromisoformat(started_date).replace(tzinfo=timezone.utc)
    weeks_elapsed = (now - started).days / 7
    return weeks_elapsed >= PHASE_REMINDER_WEEKS


def skip_exercise(data):
    """Пропускает текущее упражнение целиком (не записывая ни одного
    подхода) — переходит на следующее упражнение в дне, как будто оно
    уже пройдено. Возвращает (skipped_exercise, next_exercise) — next_
    exercise None, если пропущенное было последним в дне (день завершён
    без него). None, None если нет активного упражнения для пропуска."""
    ex, _ = current_exercise_info(data)
    if ex is None:
        return None, None

    session = data["active_session"]
    day_id = session["day_id"]
    next_order = ex["order"] + 1
    next_ex = prog.get_exercise(day_id, next_order)

    if next_ex is None:
        session["current_exercise_order"] = None
        session["current_set_number"] = None
    else:
        session["current_exercise_order"] = next_order
        session["current_set_number"] = 1

    session["resting_until"] = None
    session["reminder_sent"] = False
    return ex, next_ex


def undo_last_set(data):
    """Откатывает последнюю запись в data['sets'] (по времени ts, не по
    порядку в списке — на случай, если порядок когда-то изменится).

    Если откатываемая запись — из ТЕКУЩЕГО упражнения активной сессии
    (последний подход, который только что засчитал advance_position),
    откатывает current_set_number на 1 назад, чтобы пошаговый флоу не
    'убежал вперёд' относительно реальной истории. Откат через границу
    упражнений (когда только что перешли на новое) — редкий случай,
    сознательно не восстанавливаем точный номер подхода прошлого
    упражнения автоматически, Антон может уточнить текстом отдельно.

    Возвращает удалённую запись (dict) или None, если sets пуст."""
    sets = data.get("sets", [])
    if not sets:
        return None

    last = max(sets, key=lambda s: s["ts"])
    sets.remove(last)

    session = data.get("active_session")
    if not session or not session.get("day_id"):
        return last

    ex, set_num = current_exercise_info(data)
    if not ex:
        return last

    normalized_current = w.normalize_exercise_name(ex["name"], data.get("exercise_aliases", {}))
    if last["exercise"] == normalized_current and set_num > 1:
        session["current_set_number"] = set_num - 1

    return last


def apply_replacement(data, order, replacement_ex):
    """Применяет подтверждённую замену упражнения — сохраняет её как
    override для этого order в active_session (не в training_program.json
    — замена действует только для текущей сессии, не меняет базовую
    программу навсегда). replacement_ex — dict в том же формате, что
    упражнения программы (name/machine/sets/reps_min/reps_max/
    weight_min_kg/weight_max_kg/tempo/rest_sec/order/per_side)."""
    session = data.get("active_session")
    if not session:
        return False
    session.setdefault("exercise_overrides", {})[str(order)] = replacement_ex
    return True


def start_session(data, day_id=None):
    """Открывает сессию — запоминает момент старта, дату, день программы
    (по расписанию через program.today_day_id(), если day_id не передан
    явно) и позицию для пошагового флоу (первое упражнение, первый
    подход). Идемпотентно: повторный вызов при уже открытой сессии не
    создаёт вторую, просто ничего не делает.

    day_id можно передать явно (например, если сегодня день отдыха по
    расписанию, но Антон всё равно хочет потренироваться на замену) —
    вызывающий код в bot.py решает, спрашивать ли об этом.

    awaiting_weight_input=True сразу после старта (если day_id задан —
    для дня отдыха без плана вес спрашивать бессмысленно) — bot.py
    должен сначала спросить вес до тренировки и получить ответ, прежде
    чем показывать план/вести по упражнениям. Нужен для расчёта тоннажа
    тела (вес тела учитывается в калориях) и трекинга динамики веса."""
    if data.get("active_session"):
        return False  # уже открыта
    data["active_session"] = {
        "started_ts": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).date().isoformat(),
        "day_id": day_id,
        "current_exercise_order": 1 if day_id else None,
        "current_set_number": 1 if day_id else None,
        "resting_until": None,
        "awaiting_weight_input": bool(day_id),
        "body_weight_kg": None,
        "awaiting_wellness_input": False,  # выставляется True после ответа на вес, см. set_body_weight
        "sleep_hours": None,
        "stress_level": None,
    }
    return True


def current_exercise_info(data):
    """Возвращает (exercise_dict, set_number) для текущей позиции
    активной сессии, или (None, None) если сессия не активна, день не
    определён, или упражнения в дне кончились (день пройден).

    Приоритет модификаций плана (первое совпадение побеждает):
    1. exercise_overrides (замена упражнения через apply_replacement) —
       ручная, явная замена на другой тренажёр/упражнение целиком.
    2. targets из workouts.json (подтверждённая прогрессия через
       progression.py + confirm-кнопку) — вес/повторы того же
       упражнения подняты, само упражнение не меняется. Target — явно
       подтверждённое конкретное число, фазовый модификатор поверх
       него НЕ применяется (было бы двойной модификацией одного и
       того же намерения).
    3. Фаза периодизации (active_phase в workouts.json) — модифицирует
       СТАТИЧНЫЙ план (не target), если активная фаза не 'volume'.
    4. Статичный план из training_program.json — как задумано изначально.

    Замена действует ТОЛЬКО для этой сессии. Target — до следующего
    подтверждения (перезаписывается новым confirm, не откатывается
    автоматически). Фаза — до явной смены через set_active_phase."""
    session = data.get("active_session")
    if not session or not session.get("day_id"):
        return None, None
    order = session.get("current_exercise_order")
    if order is None:
        return None, None

    overrides = session.get("exercise_overrides", {})
    if str(order) in overrides:
        return overrides[str(order)], session.get("current_set_number", 1)

    ex = prog.get_exercise(session["day_id"], order)
    if not ex:
        return None, None  # order вышел за пределы дня — упражнения кончились

    normalized_name = w.normalize_exercise_name(ex["name"], data.get("exercise_aliases", {}))
    target = w.get_target(data, normalized_name)
    if target:
        ex = dict(ex)  # копия, не мутируем training_program.json в памяти
        ex["weight_min_kg"] = target["weight_kg"]
        ex["weight_max_kg"] = target["weight_kg"]
    else:
        active_phase = w.get_active_phase(data)
        ex = prog.apply_phase_modifier(ex, active_phase["phase_id"])
    return ex, session.get("current_set_number", 1)


def rest_timer_expired(data, now=None):
    """True, если активная сессия ждёт следующего подхода, отдых уже
    истёк (resting_until в прошлом), и напоминание об этом ЕЩЁ НЕ
    отправлено (reminder_sent=False). Используется Cron Trigger'ом
    (см. cloudflare-worker/ + timer.py) — вызывается раз в минуту,
    должен быть дешёвым и не иметь побочных эффектов сам по себе
    (пометка reminder_sent=True — отдельный шаг, mark_reminder_sent,
    вызывающий код должен явно его выполнить после реальной отправки)."""
    session = data.get("active_session")
    if not session or not session.get("resting_until"):
        return False
    if session.get("reminder_sent"):
        return False
    if now is None:
        now = datetime.now(timezone.utc)
    resting_until = datetime.fromisoformat(session["resting_until"])
    return now >= resting_until


def mark_reminder_sent(data):
    """Помечает, что напоминание об истёкшем отдыхе отправлено — не
    даёт Cron Trigger'у слать его повторно на следующих прогонах, пока
    не начнётся новый отдых (advance_position сбрасывает флаг заново)."""
    session = data.get("active_session")
    if session:
        session["reminder_sent"] = True


def extend_rest(data, extend_sec):
    """Продлевает текущий отдых на extend_sec. Возвращает новое
    resting_until (str ISO) или None, если нет активного отдыха
    (сессия не активна, или ещё ни одного подхода не сделано).

    Продление считается от МАКСИМУМА(текущий resting_until, сейчас) —
    не просто resting_until + extend_sec. Если Антон пишет 'продли',
    когда таймер уже истёк (например, напоминание уже пришло, но он
    ещё не готов), продление от старого resting_until в прошлом дало
    бы неверный результат — таймер оказался бы уже истёкшим на момент
    продления. Продлеваем от 'сейчас', если сейчас позже, чем плановое
    resting_until.

    Также сбрасывает reminder_sent=False — если напоминание уже было
    отправлено, а Антон просит продлить, значит фактически отдых ещё
    не закончился для него, и повторное напоминание должно прийти снова
    после нового (продлённого) resting_until."""
    session = data.get("active_session")
    if not session or not session.get("resting_until"):
        return None

    now = datetime.now(timezone.utc)
    current_resting_until = datetime.fromisoformat(session["resting_until"])
    base = max(now, current_resting_until)
    new_resting_until = base + timedelta(seconds=extend_sec)

    session["resting_until"] = new_resting_until.isoformat()
    session["reminder_sent"] = False
    return session["resting_until"]


def advance_position(data, weight_kg, reps, rpe=None, note=""):
    """Записывает выполненный сет текущего упражнения через
    workouts.add_set, продвигает позицию сессии на следующий подход
    или следующее упражнение.

    Возвращает dict с ключами:
        "recorded_exercise": имя записанного упражнения (или None,
            если сессия/позиция невалидна — ничего не записано)
        "exercise_complete": True, если это был ПОСЛЕДНИЙ подход
            текущего упражнения (неважно, есть ли следующее упражнение
            в дне или это конец дня) — сигнал для bot.py показать
            план/факт статистику по только что завершённому упражнению
        "completed_exercise": полный dict упражнения из программы
            (для plan/fact сравнения) — совпадает с ex, если
            exercise_complete=True, иначе None
        "day_complete": True, если это был последний сет последнего
            упражнения дня — вызывающий код может предложить сразу
            завершить сессию
        "next_exercise": следующее упражнение (dict) или None, если
            день завершён
        "next_set_number": номер следующего подхода в next_exercise,
            актуален только если next_exercise задан и это ТО ЖЕ
            упражнение (не первый подход нового)
        "rest_sec": сколько отдыхать после записанного сета — берётся
            из ТОЛЬКО ЧТО записанного упражнения (rest после ЭТОГО
            подхода), не следующего
    """
    ex, set_number = current_exercise_info(data)
    if ex is None:
        return {"recorded_exercise": None, "exercise_complete": False,
                "completed_exercise": None, "day_complete": False,
                "next_exercise": None, "next_set_number": None, "rest_sec": None}

    session = data["active_session"]
    day_id = session["day_id"]

    w.add_set(data, ex["name"], session["date"], weight_kg, reps,
              set_number, rpe=rpe, note=note)

    rest_sec = ex["rest_sec"]

    # Таймер отдыха: Cron Trigger (см. cloudflare-worker/, каждую минуту)
    # сравнивает resting_until с текущим временем и шлёт напоминание,
    # если время истекло и напоминание ещё не отправлено. reminder_sent
    # сбрасывается в False на каждый новый подход — без этого напоминание
    # ушло бы повторно на следующей минуте после того, как уже сработало.
    session["resting_until"] = (datetime.now(timezone.utc) + timedelta(seconds=rest_sec)).isoformat()
    session["reminder_sent"] = False

    if set_number < ex["sets"]:
        # ещё есть подходы в этом же упражнении
        session["current_set_number"] = set_number + 1
        return {
            "recorded_exercise": ex["name"], "exercise_complete": False,
            "completed_exercise": None, "day_complete": False,
            "next_exercise": ex, "next_set_number": set_number + 1,
            "rest_sec": rest_sec,
        }

    # подходы этого упражнения закончились — следующее упражнение в дне
    next_order = ex["order"] + 1
    next_ex = prog.get_exercise(day_id, next_order)
    if next_ex is None:
        # это было последнее упражнение дня
        return {
            "recorded_exercise": ex["name"], "exercise_complete": True,
            "completed_exercise": ex, "day_complete": True,
            "next_exercise": None, "next_set_number": None,
            "rest_sec": rest_sec,
        }

    session["current_exercise_order"] = next_order
    session["current_set_number"] = 1
    return {
        "recorded_exercise": ex["name"], "exercise_complete": True,
        "completed_exercise": ex, "day_complete": False,
        "next_exercise": next_ex, "next_set_number": 1,
        "rest_sec": rest_sec,
    }


def end_session(data):
    """Закрывает сессию, возвращает dict с данными для построения отчёта,
    либо None если сессия не была открыта — вызывающий код должен явно
    обработать этот случай.

    Возвращает dict (не tuple — сигнатура уже дважды росла при добавлении
    day_id, потом веса/калорий; словарь не потребует менять вызовы снова
    при следующем требовании):
        "exercises": список нормализованных имён упражнений сессии
        "date": дата сессии (YYYY-MM-DD)
        "day_id": id дня программы или None
        "body_weight_kg": вес тела, введённый в начале, или None
        "duration_minutes": продолжительность тренировки в минутах
        "sleep_hours": часы сна из дневника самочувствия, или None
        "stress_level": уровень стресса 1-10, или None
        "wellness_note": свободный текст ответа на вопрос о самочувствии
    Все поля возвращаются ОТДЕЛЬНО от active_session, потому что
    active_session обнуляется тут же."""
    session = data.get("active_session")
    if not session:
        return None

    session_date = session["date"]
    today_sets = [s for s in data.get("sets", []) if s["date"] == session_date]
    exercises_today = sorted({s["exercise"] for s in today_sets})

    duration_minutes = cal.session_duration_minutes(session.get("started_ts"))

    result = {
        "exercises": exercises_today,
        "date": session_date,
        "day_id": session.get("day_id"),
        "body_weight_kg": session.get("body_weight_kg"),
        "duration_minutes": duration_minutes,
        "sleep_hours": session.get("sleep_hours"),
        "stress_level": session.get("stress_level"),
        "wellness_note": session.get("wellness_note", ""),
    }

    # ПОСТОЯННОЕ хранилище по дате — раньше самочувствие исчезало вместе
    # с active_session сразу после отчёта, теперь progression.py может
    # проверить самочувствие КОНКРЕТНОЙ прошедшей тренировки при решении
    # о прогрессии. Сохраняем только если хоть одно значение задано —
    # не создаём пустую запись 'заполнил, но всё None' без причины.
    if session.get("sleep_hours") is not None or session.get("stress_level") is not None:
        w.save_wellness_for_date(data, session_date, session.get("sleep_hours"), session.get("stress_level"))

    # Тот же принцип для веса тела — раньше вводился перед каждой
    # тренировкой (нужен для калорий), но нигде не сохранялся постоянно
    # после отчёта. Нужен для трекера цели по весу — динамика/темп
    # требуют истории по датам.
    if session.get("body_weight_kg") is not None:
        w.save_weight_for_date(data, session_date, session["body_weight_kg"])

    data["active_session"] = None
    return result


def is_session_active(data):
    return bool(data.get("active_session"))


def is_awaiting_weight_input(data):
    """True, если сессия открыта и ждёт ответа на вопрос о весе тела до
    тренировки — весь остальной флоу (план, подходы) должен подождать."""
    session = data.get("active_session")
    return bool(session and session.get("awaiting_weight_input"))


def is_awaiting_wellness_input(data):
    """True, если сессия ждёт ответа на вопрос о сне/стрессе — второй
    предтренировочный вопрос, после веса, до показа плана."""
    session = data.get("active_session")
    return bool(session and session.get("awaiting_wellness_input"))


def parse_wellness_answer(text):
    """Парсит ответ на вопрос о сне/стрессе — гибкий формат, не строгая
    схема. Понимает:
    - 'спал 7, стресс 4' -> sleep_hours=7.0, stress_level=4
    - 'сон 6 часов' -> sleep_hours=6.0, stress_level=None
    - 'нормально' / 'хорошо выспался' / 'плохо' -> оба None, но текст
      сохраняется как raw_note (не теряем информацию, просто не смогли
      извлечь структурированные числа)
    Детерминированно (regex), не DeepSeek — числа часов/уровня извлекаются
    по позиции рядом с ключевыми словами 'сон'/'спал'/'стресс', не
    требуют понимания сложного контекста.

    Возвращает dict {"sleep_hours": float|None, "stress_level": int|None,
    "raw_note": str} — всегда непустой, никогда не 'не понял' (в отличие
    от parse_weight_kg, здесь нет обязательного числа, свободный ответ
    типа 'нормально' — валидный самодостаточный ответ)."""
    t = text.strip().lower()

    sleep_hours = None
    sleep_match = re.search(r"(?:сон|спал|спала)\D{0,10}?(\d+(?:[.,]\d+)?)", t)
    if sleep_match:
        sleep_hours = float(sleep_match.group(1).replace(",", "."))

    stress_level = None
    stress_match = re.search(r"стресс\D{0,10}?(\d+)", t)
    if stress_match:
        stress_level = int(stress_match.group(1))

    return {"sleep_hours": sleep_hours, "stress_level": stress_level, "raw_note": text.strip()}


def set_wellness(data, sleep_hours, stress_level, raw_note=""):
    """Сохраняет дневник самочувствия для сессии, снимает флаг ожидания.
    Возвращает True, если сессия была активна, False иначе (не падает
    на некорректном состоянии)."""
    session = data.get("active_session")
    if not session:
        return False
    session["sleep_hours"] = sleep_hours
    session["stress_level"] = stress_level
    session["wellness_note"] = raw_note
    session["awaiting_wellness_input"] = False
    return True


def set_body_weight(data, weight_kg):
    """Сохраняет вес тела для этой сессии, снимает флаг ожидания веса,
    ставит флаг ожидания дневника самочувствия (сон/стресс — следующий
    вопрос перед показом плана). Возвращает True, если сессия была
    активна и ждала веса, False если нет активной сессии (вызывающий
    код не должен был сюда попасть, но функция не падает на некорректном
    состоянии, просто ничего не делает)."""
    session = data.get("active_session")
    if not session:
        return False
    session["body_weight_kg"] = weight_kg
    session["awaiting_weight_input"] = False
    session["awaiting_wellness_input"] = True
    return True


def build_session_report(data, exercises, session_date, day_id=None,
                          body_weight_kg=None, duration_minutes=None,
                          sleep_hours=None, stress_level=None):
    """Строит текст отчёта: заголовок + дневник самочувствия (если
    заполнен), по каждому упражнению сессии —
    (1) план/факт: сравнение фактических подходов с планом программы,
        если day_id известен и упражнение реально есть в плане этого
        дня (может не быть — например, если Антон записал что-то не
        по плану текстом отдельно);
    (2) тренд: сравнение с предыдущей тренировкой этого упражнения
        (тоннаж туда-сюда), если такая история есть.
    В конце — общий тоннаж тренировки (сумма вес×повторы по всем сетам)
    и оценка калорий (calories.estimate_calories, MET-формула), если
    body_weight_kg и duration_minutes заданы. Без них — эта секция не
    показывается, не выдумываем цифры на отсутствующих данных.

    day_id=None (например, если тренировка началась в день отдыха без
    явного плана) -> план/факт не показывается, только тренд, как было
    раньше — обратная совместимость с тренировками без day_id."""
    if not exercises:
        return "Тренировка завершена, но ни одного подхода не записано."

    lines = ["\U0001f3c1 <b>Тренировка завершена</b>"]
    if sleep_hours is not None or stress_level is not None:
        wellness_parts = []
        if sleep_hours is not None:
            wellness_parts.append(f"сон {sleep_hours}ч")
        if stress_level is not None:
            wellness_parts.append(f"стресс {stress_level}/10")
        lines.append(f"\U0001f634 {', '.join(wellness_parts)}")
    lines[-1] += "\n"  # пустая строка после последней строки заголовка/самочувствия

    day_plan = prog.get_day_plan(day_id) if day_id else None
    total_tonnage = 0.0

    for exercise in exercises:
        history = w.get_history_for_exercise(data, exercise, limit_sessions=5)
        today_sessions = [h for h in history if h["date"] == session_date]
        if not today_sessions:
            continue
        today_sets = today_sessions[0]["sets"]
        total_tonnage += sum(
            (s.get("weight_kg") or 0) * s.get("reps", 0)
            for s in today_sets if w.is_countable_for_tonnage(s)
        )

        plan_ex = None
        if day_plan:
            for ex in day_plan["exercises"]:
                if w.normalize_exercise_name(ex["name"], data.get("exercise_aliases", {})) == exercise:
                    plan_ex = ex
                    break

        if plan_ex:
            summary = prog.format_exercise_plan_vs_fact(plan_ex, today_sets)
        else:
            summary = _format_exercise_today(exercise, today_sets)

        prior_sessions = [h for h in history if h["date"] < session_date]
        if prior_sessions:
            trend = _format_trend(today_sets, prior_sessions[-1]["sets"])
            summary += f"\n  {trend}"
        lines.append(summary)

    totals = [f"\U0001f4ca <b>Итого тоннаж:</b> {round(total_tonnage)} кг"]
    cardio = w.get_cardio_for_date(data, session_date)
    if cardio["total_km"] > 0:
        totals.append(f"\U0001f6b4 <b>Кардио:</b> {cardio['total_km']} км")
    calories = cal.estimate_calories(body_weight_kg, duration_minutes)
    if calories is not None:
        totals.append(f"\U0001f525 <b>Оценка калорий:</b> ~{calories} ккал "
                       f"(при весе {body_weight_kg} кг, {duration_minutes:.0f} мин)")
    lines.append("\n".join(totals))

    return "\n\n".join(lines)


def _format_exercise_today(exercise, today_sets):
    parts = []
    for s in today_sets:
        weight = f"{s['weight_kg']}\u00d7" if s.get("weight_kg") else ""
        parts.append(f"{weight}{s['reps']}")
    return f"<b>{exercise}</b>: " + ", ".join(parts)


def _format_trend(today_sets, prior_sets):
    """Сравнивает суммарный тоннаж (вес x повторы, просуммировано по
    сетам, БЕЗ разминочных подходов) сегодня против прошлой сессии —
    простая, понятная метрика тренда, не требующая сложной статистики."""
    today_volume = sum(
        (s.get("weight_kg") or 0) * s.get("reps", 0)
        for s in today_sets if w.is_countable_for_tonnage(s)
    )
    prior_volume = sum(
        (s.get("weight_kg") or 0) * s.get("reps", 0)
        for s in prior_sets if w.is_countable_for_tonnage(s)
    )

    if prior_volume == 0:
        return "первая тренировка с весом по этому упражнению"

    diff_pct = round((today_volume - prior_volume) / prior_volume * 100)
    if diff_pct > 0:
        return f"\U0001f4c8 тоннаж +{diff_pct}% к прошлой тренировке"
    elif diff_pct < 0:
        return f"\U0001f4c9 тоннаж {diff_pct}% к прошлой тренировке"
    return "тоннаж как в прошлый раз"
