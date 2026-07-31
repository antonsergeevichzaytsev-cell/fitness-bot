"""Парсинг свободного текста о тренировке через DeepSeek.

Вход: "жим лежа гантелями 30 на 10 три подхода, последний тяжело пошёл"
Выход: список структурированных сетов (см. SCHEMA.md), готовых к
workouts.add_set — с одной оговоркой: safety-проверка здесь НЕ
выполняется, это отдельный шаг в вызывающем коде (net не полагается на
промпт как единственную защиту, см. safety.py).

Философия промпта, унаследованная из filings.py/pipeline_sync.py:
не угадывать неоднозначное. Если из текста непонятен вес, повторы или
число подходов — DeepSeek возвращает uncertain=true с вопросом, а не
придумывает правдоподобное число. Тренировочный вес — не та вещь, где
уместна эвристика "наверное имел в виду".
"""
import json
import os
import sys
import urllib.request

import net

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

SYS_PROMPT = (
    "Ты парсишь сообщение о силовой тренировке в структурированные данные. "
    "Пользователь пишет свободным текстом на русском, например "
    "\"жим лежа гантелями 30 на 10 три подхода\" или "
    "\"присед 60х8, потом 60х8 ещё раз, тяжело пошло\". "
    "\n\n"
    "ПРАВИЛО: не угадывать. Если из текста нельзя однозначно извлечь "
    "упражнение, вес и повторы — верни uncertain=true с конкретным "
    "вопросом, а не придумывай правдоподобные числа. Тренировочный вес "
    "— не та вещь, где уместна догадка \"наверное имел в виду\"; "
    "неверная запись веса портит историю прогрессии на много тренировок "
    "вперёд, гораздо дешевле переспросить один раз. "
    "\n\n"
    "Один подход = один элемент в sets. \"30 на 10 три подхода\" -> "
    "ТРИ элемента с одинаковым весом/повторами (если явно не сказано, "
    "что подходы разные). \"30х10, 30х10, 27.5х8\" -> три элемента с "
    "разными весами, как написано. Если написано только одно число "
    "подходов без детализации по каждому — считай, что все подходы "
    "одинаковые (вес и повторы), пока пользователь явно не укажет "
    "разбивку. "
    "\n\n"
    "Вес без единиц измерения (просто число) считай в кг — это "
    "силовая тренировка в тренажёрном зале, не бег. Вес может быть "
    "дробным (27.5, 32.5 — стандартные шаги гантелей/блинов). "
    "\n\n"
    "rpe (Rate of Perceived Exertion, 1-10) заполняй ТОЛЬКО если "
    "пользователь явно описал сложность словами: \"тяжело\"/\"на грани\" "
    "-> rpe 8-9, \"легко\"/\"без напряга\" -> rpe 5-6, \"средне\"/\"нормально\" "
    "-> rpe 7. Если пользователь не упомянул сложность вообще — rpe null, "
    "не придумывай его из общих соображений про вес/повторы. "
    "\n\n"
    "set_type — тип подхода, ТОЛЬКО если пользователь явно указал словом: "
    "\"разминка\"/\"разминочный\" -> \"warmup\", \"дропсет\"/\"дроп-сет\"/\"дроп сет\" "
    "-> \"dropset\", \"отказ\"/\"до отказа\"/\"на отказ\" -> \"failure\". Если тип не "
    "упомянут явно — \"normal\" (обычный рабочий подход), не выдумывай тип "
    "по контексту (высокий вес сам по себе не значит 'не разминка' и т.п.). "
    "\n\n"
    "Reply ONLY with valid JSON: "
    "{\"uncertain\": bool, \"question\": str, \"sets\": ["
    "{\"exercise\": str, \"weight_kg\": float_or_null, \"reps\": int, \"rpe\": int_or_null, "
    "\"note\": str, \"set_type\": str}"
    "]}. "
    "Если uncertain=true, sets может быть пустым списком, question "
    "обязателен и должен быть конкретным вопросом на русском "
    "(\"Какой был вес на приседе?\", не общее \"уточни детали\"). "
    "Если uncertain=false, question — пустая строка."
)


def parse_workout_text(text, max_tokens=500):
    """Отправляет text в DeepSeek, возвращает распарсенный dict.

    Возвращает:
        {"uncertain": False, "sets": [...]} — успешно распарсено
        {"uncertain": True, "question": "..."} — нужно уточнение у пользователя
        {"uncertain": True, "question": "Не удалось разобрать...", "error": True}
            — технический сбой (DeepSeek недоступен, битый JSON и т.п.),
            отличается от осознанного uncertain модели полем "error"
    """
    if not DEEPSEEK_KEY:
        return {"uncertain": True, "question": "DEEPSEEK_API_KEY не настроен — не могу разобрать текст.", "error": True}

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.1,  # низкая — это извлечение фактов, не творческая задача
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=data,
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
    )
    try:
        with net.urlopen_retry(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as e:
        print(f"  ! parser error: {e}", file=sys.stderr)
        return {
            "uncertain": True,
            "question": "Не удалось разобрать сообщение — DeepSeek недоступен или вернул что-то неожиданное. Попробуй написать ещё раз, может быть проще (например: 'присед 60 на 8, 3 подхода').",
            "error": True,
        }

    if not isinstance(parsed, dict) or "sets" not in parsed:
        return {
            "uncertain": True,
            "question": "Не получилось разобрать ответ модели — попробуй переформулировать сообщение.",
            "error": True,
        }

    return parsed


REPLACEMENT_SYS_PROMPT = (
    "Ты предлагаешь замену силового упражнения — тренажёр занят, "
    "сломан, или пользователь хочет его заменить. Дай ОДНО альтернативное "
    "упражнение с тем же паттерном движения (та же мышечная группа, "
    "похожий вектор нагрузки) на доступном оборудовании тренажёрного зала. "
    "\n\n"
    "СТОП-ЛИСТ — НИКОГДА не предлагай: жим штанги от груди/лёжа, "
    "приседания или гакк-приседания со штангой, выпады любые, жим ногами "
    "(leg press), разгибание ног сидя (leg extension), румынская тяга со "
    "штангой, бег/прыжки/степ-аэробика/плиометрика. Это медицинские "
    "ограничения (травма коленей), не пожелание — нарушать нельзя ни при "
    "каких обстоятельствах, даже если пользователь сам предложит что-то "
    "из списка. "
    "\n\n"
    "Предложи вес/повторы/темп/отдых для замены, ориентируясь на "
    "параметры заменяемого упражнения (похожая интенсивность), не выдумывай "
    "точные числа с нуля — экстраполируй логично. "
    "\n\n"
    "Reply ONLY with valid JSON: "
    '{"replacement_name": str, "machine": str, "reasoning": str, '
    '"weight_min_kg": float_or_null, "weight_max_kg": float_or_null, '
    '"reps_min": int, "reps_max": int, "tempo": str, "rest_sec": int}. '
    "reasoning — короткое объяснение на русском, почему это хорошая замена "
    "(тот же паттерн движения). weight_min_kg/max — null, если вес "
    "'по ощущению' уместнее для этой замены."
)


def suggest_replacement(original_exercise, reason=""):
    """Предлагает замену упражнению через DeepSeek. original_exercise —
    dict из training_program.json (полный план упражнения, не только
    имя) — модель видит параметры оригинала для калибровки замены.

    Возвращает dict с ключом replacement_name и параметрами замены, ИЛИ
    {"error": True, ...} при сбое сети/парсинга. ВАЖНО: результат этой
    функции НЕ финальная защита от запрещённых упражнений — промпт явно
    называет стоп-лист, но код (safety.check_exercise на результат)
    ДОЛЖЕН быть проверен вызывающим кодом (bot.py) перед тем, как
    показать предложение пользователю — промпт может быть проигнорирован
    моделью, код-проверка не может (тот же принцип, что и в safety.py)."""
    if not DEEPSEEK_KEY:
        return {"error": True, "question": "DEEPSEEK_API_KEY не настроен — не могу предложить замену."}

    user_msg = (
        f"Заменить: {original_exercise['name']} ({original_exercise['machine']}), "
        f"план {original_exercise['sets']}x{original_exercise['reps_min']}-{original_exercise['reps_max']}. "
        f"Причина: {reason or 'не указана'}."
    )
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": REPLACEMENT_SYS_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_URL, data=data,
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
    )
    try:
        with net.urlopen_retry(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        content = resp["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except Exception as e:
        print(f"  ! replacement suggestion error: {e}", file=sys.stderr)
        return {"error": True, "question": "Не удалось предложить замену — DeepSeek недоступен."}

    if not isinstance(parsed, dict) or "replacement_name" not in parsed:
        return {"error": True, "question": "Не получилось разобрать предложение замены."}

    return parsed
