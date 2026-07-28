"""Тесты для workouts.py — хранилище тренировок, нормализация упражнений
через алиасы, история по упражнению, targets.
"""
import sys

sys.path.insert(0, "..")
import workouts as w


# --- load_workouts / save_workouts ------------------------------------

def test_load_workouts_missing_file_returns_empty_schema(tmp_path, monkeypatch):
    fake_path = tmp_path / "workouts.json"
    monkeypatch.setattr(w, "WORKOUTS_PATH", str(fake_path))
    data = w.load_workouts()
    assert data == {"schema_version": 1, "sets": [], "exercise_aliases": {},
                     "pending_suggestions": [], "targets": {}}


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    fake_path = tmp_path / "workouts.json"
    monkeypatch.setattr(w, "WORKOUTS_PATH", str(fake_path))
    data = w.load_workouts()
    w.add_set(data, "тест", "2026-07-28", 10.0, 5, 1)
    w.save_workouts(data)
    loaded = w.load_workouts()
    assert len(loaded["sets"]) == 1
    assert loaded["sets"][0]["exercise"] == "тест"


# --- normalize_exercise_name --------------------------------------------

def test_normalize_new_exercise_returns_lowercased_raw():
    result = w.normalize_exercise_name("Присед", {})
    assert result == "присед"


def test_normalize_matches_known_normalized_name():
    aliases = {"жим лёжа гантели": ["гантели лежа"]}
    assert w.normalize_exercise_name("жим лёжа гантели", aliases) == "жим лёжа гантели"


def test_normalize_matches_alias():
    aliases = {"жим лёжа гантели": ["гантели лежа", "жим гантелями лёжа"]}
    assert w.normalize_exercise_name("гантели лежа", aliases) == "жим лёжа гантели"


def test_normalize_case_insensitive():
    aliases = {"жим лёжа гантели": ["гантели лежа"]}
    assert w.normalize_exercise_name("ГАНТЕЛИ ЛЕЖА", aliases) == "жим лёжа гантели"


def test_normalize_strips_whitespace():
    assert w.normalize_exercise_name("  присед  ", {}) == "присед"


# --- add_set -------------------------------------------------------------

def test_add_set_creates_entry_with_all_fields():
    data = w.load_workouts()
    entry = w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1, rpe=7, note="легко")
    assert entry["exercise"] == "присед"
    assert entry["exercise_raw"] == "присед"
    assert entry["weight_kg"] == 50.0
    assert entry["reps"] == 8
    assert entry["set_number"] == 1
    assert entry["rpe"] == 7
    assert entry["note"] == "легко"
    assert entry["id"].startswith("s_")


def test_add_set_appends_to_data_sets():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 2)
    assert len(data["sets"]) == 2


def test_add_set_uses_alias_normalization():
    data = w.load_workouts()
    w.add_alias(data, "жим лёжа гантели", "гантели лежа")
    entry = w.add_set(data, "гантели лежа", "2026-07-28", 30.0, 10, 1)
    assert entry["exercise"] == "жим лёжа гантели"
    assert entry["exercise_raw"] == "гантели лежа"


def test_add_set_ids_are_unique():
    data = w.load_workouts()
    e1 = w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    e2 = w.add_set(data, "присед", "2026-07-28", 50.0, 8, 2)
    assert e1["id"] != e2["id"]


# --- get_history_for_exercise --------------------------------------------

def test_history_groups_by_date():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 2)
    w.add_set(data, "присед", "2026-07-22", 52.5, 8, 1)
    history = w.get_history_for_exercise(data, "присед")
    assert len(history) == 2
    assert history[0]["date"] == "2026-07-20"
    assert len(history[0]["sets"]) == 2
    assert history[1]["date"] == "2026-07-22"


def test_history_only_matching_exercise():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    w.add_set(data, "жим лёжа", "2026-07-20", 30.0, 10, 1)
    history = w.get_history_for_exercise(data, "присед")
    assert len(history) == 1
    assert all(s["exercise"] == "присед" for day in history for s in day["sets"])


def test_history_sorted_chronologically():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-22", 52.5, 8, 1)
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    history = w.get_history_for_exercise(data, "присед")
    assert [h["date"] for h in history] == ["2026-07-20", "2026-07-22"]


def test_history_limits_to_last_n_sessions():
    data = w.load_workouts()
    for day in range(1, 6):
        w.add_set(data, "присед", f"2026-07-{10+day:02d}", 50.0, 8, 1)
    history = w.get_history_for_exercise(data, "присед", limit_sessions=3)
    assert len(history) == 3
    assert history[-1]["date"] == "2026-07-15"  # последние 3 из 5


def test_history_empty_for_unknown_exercise():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    history = w.get_history_for_exercise(data, "жим штанги лёжа")
    assert history == []


# --- targets ---------------------------------------------------------------

def test_get_target_none_when_not_set():
    data = w.load_workouts()
    assert w.get_target(data, "присед") is None


def test_set_and_get_target():
    data = w.load_workouts()
    w.set_target(data, "присед", 55.0, 8)
    target = w.get_target(data, "присед")
    assert target["weight_kg"] == 55.0
    assert target["reps"] == 8
    assert "set_at" in target


# --- add_alias -------------------------------------------------------------

def test_add_alias_idempotent():
    data = w.load_workouts()
    w.add_alias(data, "присед", "приседания")
    w.add_alias(data, "присед", "приседания")
    assert data["exercise_aliases"]["присед"] == ["приседания"]


def test_add_alias_case_insensitive_dedup():
    data = w.load_workouts()
    w.add_alias(data, "присед", "Приседания")
    w.add_alias(data, "присед", "приседания")
    assert len(data["exercise_aliases"]["присед"]) == 1


def test_add_alias_skips_when_same_as_normalized():
    data = w.load_workouts()
    w.add_alias(data, "присед", "присед")
    assert data["exercise_aliases"].get("присед", []) == []


# --- known_exercises ---------------------------------------------------

def test_known_exercises_lists_unique_sorted():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    w.add_set(data, "жим лёжа", "2026-07-20", 30.0, 10, 1)
    w.add_set(data, "присед", "2026-07-21", 50.0, 8, 1)
    assert w.known_exercises(data) == ["жим лёжа", "присед"]


def test_known_exercises_empty_for_no_sets():
    data = w.load_workouts()
    assert w.known_exercises(data) == []
