"""Проверка упражнений против медицинских ограничений (safety_constraints.json).

Работает в коде, не в промпте — промпт DeepSeek может быть проигнорирован
моделью (галлюцинация, неудачный парсинг), код-проверка не может. Это
последняя линия защиты перед тем, как бот предложит прогрессию веса.

Матчинг простой (substring, регистронезависимо) — намеренно: точный
список запрещённых паттернов лучше, чем "умный" NLP-матчинг, который
может пропустить редкую формулировку. Если Антон напишет упражнение,
которого нет в списке matches, но оно фактически совпадает с
запрещённым паттерном — это словарь нужно расширить, а не полагаться
на эвристику угадать намерение.
"""
import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONSTRAINTS_PATH = os.path.join(ROOT, "safety_constraints.json")


def load_constraints():
    with open(CONSTRAINTS_PATH, encoding="utf-8") as f:
        return json.load(f)


def check_exercise(exercise_name, constraints=None):
    """Проверяет название упражнения против banned/caution паттернов.

    Возвращает dict:
        {"status": "ok"} — без ограничений, прогрессия разрешена как обычно
        {"status": "hard_block", "pattern": str, "reason": str} — прогрессия
            запрещена категорически
        {"status": "manual_progression_only", "pattern": str, "reason": str} —
            прогрессия не предлагается автоматически, только по явному
            запросу Антона

    Матчинг через substring на нижнем регистре. Пустое/None имя ->
    "ok" (нечего проверять, не блокируем на пустоте).
    """
    if not exercise_name:
        return {"status": "ok"}

    if constraints is None:
        constraints = load_constraints()

    name_lower = exercise_name.lower()

    for pattern_key, pattern in constraints.get("banned_movement_patterns", {}).items():
        for match in pattern.get("matches", []):
            if match.lower() in name_lower:
                return {
                    "status": "hard_block",
                    "pattern": pattern_key,
                    "reason": pattern.get("reason", ""),
                }

    for pattern_key, pattern in constraints.get("caution_movement_patterns", {}).items():
        for match in pattern.get("matches", []):
            if match.lower() in name_lower:
                return {
                    "status": "manual_progression_only",
                    "pattern": pattern_key,
                    "reason": pattern.get("reason", ""),
                }

    return {"status": "ok"}


def get_profile(constraints=None):
    """Возвращает физический профиль (рост/вес) для контекста в промптах."""
    if constraints is None:
        constraints = load_constraints()
    return constraints.get("profile", {})
