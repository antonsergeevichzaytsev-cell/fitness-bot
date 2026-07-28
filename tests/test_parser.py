"""Тесты для parser.py — парсинг свободного текста через DeepSeek.

Все тесты мокируют net.urlopen_retry — реальная сеть к api.deepseek.com
недоступна из этой тестовой среды (egress allowlist), и даже если бы
была доступна, юнит-тесты не должны зависеть от живого API (недетерминизм
LLM, стоимость вызовов, флейки от сетевых сбоев). Проверяем контракт
функции: как parser.py обрабатывает то, что вернул (или не вернул) API,
а не качество самих ответов DeepSeek — то отдельная задача (eval), не
unit-тест.
"""
import json
import os
import sys
from unittest import mock

os.environ.setdefault("DEEPSEEK_API_KEY", "test")

sys.path.insert(0, "..")
import parser


def _fake_deepseek_response(parsed_content):
    """Строит объект, имитирующий контекстный менеджер, который вернул
    бы net.urlopen_retry — с телом ответа DeepSeek Chat Completions API."""
    body = json.dumps({
        "choices": [{"message": {"content": json.dumps(parsed_content, ensure_ascii=False)}}]
    }).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    return FakeResponse()


# --- успешный парсинг -------------------------------------------------

def test_parse_workout_returns_sets_on_success():
    fake = _fake_deepseek_response({
        "uncertain": False, "question": "",
        "sets": [{"exercise": "присед", "weight_kg": 50.0, "reps": 8, "rpe": None, "note": ""}],
    })
    with mock.patch("parser.net.urlopen_retry", return_value=fake):
        result = parser.parse_workout_text("присед 50 на 8")
    assert result["uncertain"] is False
    assert len(result["sets"]) == 1
    assert result["sets"][0]["exercise"] == "присед"
    assert result["sets"][0]["weight_kg"] == 50.0


def test_parse_workout_multiple_sets_same_weight():
    fake = _fake_deepseek_response({
        "uncertain": False, "question": "",
        "sets": [
            {"exercise": "жим лёжа гантели", "weight_kg": 30.0, "reps": 10, "rpe": None, "note": ""},
            {"exercise": "жим лёжа гантели", "weight_kg": 30.0, "reps": 10, "rpe": None, "note": ""},
            {"exercise": "жим лёжа гантели", "weight_kg": 30.0, "reps": 8, "rpe": 8, "note": "тяжело"},
        ],
    })
    with mock.patch("parser.net.urlopen_retry", return_value=fake):
        result = parser.parse_workout_text("жим лежа гантелями 30 на 10 три подхода, последний тяжело")
    assert len(result["sets"]) == 3
    assert result["sets"][2]["rpe"] == 8


# --- uncertain: модель осознанно просит уточнение -------------------------

def test_parse_workout_uncertain_when_weight_missing():
    fake = _fake_deepseek_response({
        "uncertain": True,
        "question": "Какой был вес на приседе?",
        "sets": [],
    })
    with mock.patch("parser.net.urlopen_retry", return_value=fake):
        result = parser.parse_workout_text("присед сделал 3 подхода по 8")
    assert result["uncertain"] is True
    assert "вес" in result["question"].lower()
    assert result["sets"] == []


def test_parse_workout_uncertain_has_no_error_flag():
    # Осознанный uncertain от модели — не то же самое, что технический сбой
    fake = _fake_deepseek_response({"uncertain": True, "question": "Уточни вес", "sets": []})
    with mock.patch("parser.net.urlopen_retry", return_value=fake):
        result = parser.parse_workout_text("что-то неясное")
    assert "error" not in result


# --- технические сбои: сеть, битый JSON -------------------------------

def test_parse_workout_network_error_returns_error_flag():
    with mock.patch("parser.net.urlopen_retry", side_effect=Exception("connection reset")):
        result = parser.parse_workout_text("присед 50 на 8")
    assert result["uncertain"] is True
    assert result.get("error") is True


def test_parse_workout_missing_api_key_returns_error_without_network_call():
    with mock.patch("parser.DEEPSEEK_KEY", ""):
        result = parser.parse_workout_text("присед 50 на 8")
    assert result["uncertain"] is True
    assert result.get("error") is True
    assert "DEEPSEEK_API_KEY" in result["question"]


def test_parse_workout_malformed_json_content_returns_error():
    body = json.dumps({"choices": [{"message": {"content": "not valid json{{{"}}]}).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    with mock.patch("parser.net.urlopen_retry", return_value=FakeResponse()):
        result = parser.parse_workout_text("присед 50 на 8")
    assert result["uncertain"] is True
    assert result.get("error") is True


def test_parse_workout_response_missing_sets_key_returns_error():
    fake = _fake_deepseek_response({"uncertain": False, "question": ""})  # нет "sets"
    with mock.patch("parser.net.urlopen_retry", return_value=fake):
        result = parser.parse_workout_text("присед 50 на 8")
    assert result["uncertain"] is True
    assert result.get("error") is True
