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
    "Reply ONLY with valid JSON: "
    "{\"uncertain\": bool, \"question\": str, \"sets\": ["
    "{\"exercise\": str, \"weight_kg\": float_or_null, \"reps\": int, \"rpe\": int_or_null, \"note\": str}"
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
