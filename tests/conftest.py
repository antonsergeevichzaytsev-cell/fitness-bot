"""Автоматическая изоляция workouts.py.WORKOUTS_PATH для КАЖДОГО теста.

Найдено 28.07.2026: после того как Антон реально записал первый подход
через живого бота, workouts.json в репозитории перестал быть пустым —
и 10 тестов, вызывавших w.load_workouts() без явного monkeypatch,
внезапно начали читать реальные production-данные вместо чистого
состояния. Это не единичная заплатка по каждому тесту — системная
проблема: любой новый тест, забывший про изоляцию, рано или поздно
столкнётся с тем же самым, когда в workouts.json появятся новые записи.

autouse=True гарантирует, что WORKOUTS_PATH подменяется на временный
файл ПЕРЕД каждым тестом автоматически, без явного запроса фикстуры —
тесты продолжают писать `w.load_workouts()` как раньше, не думая об
изоляции вручную.
"""
import sys

sys.path.insert(0, "..")

import pytest


@pytest.fixture(autouse=True)
def isolate_workouts_file(tmp_path, monkeypatch):
    import workouts as w
    fake_path = tmp_path / "workouts.json"
    monkeypatch.setattr(w, "WORKOUTS_PATH", str(fake_path))
    yield
