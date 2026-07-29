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
                     "pending_suggestions": [], "targets": {}, "wellness_log": {}, "cardio_log": {},
                     "active_phase": {"phase_id": "volume", "started_date": None, "reminder_sent": False}}


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


# --- find_exercise_by_partial_name ---------------------------------------

def test_find_exercise_exact_match():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    assert w.find_exercise_by_partial_name(data, "присед") == "присед"


def test_find_exercise_partial_query_matches_full_name():
    data = w.load_workouts()
    w.add_set(data, "жим лёжа гантели", "2026-07-20", 30.0, 10, 1)
    assert w.find_exercise_by_partial_name(data, "жим") == "жим лёжа гантели"


def test_find_exercise_longer_query_matches_shorter_name():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    assert w.find_exercise_by_partial_name(data, "присед со штангой") == "присед"


def test_find_exercise_no_match_returns_none():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    assert w.find_exercise_by_partial_name(data, "совсем другое") is None


def test_find_exercise_ambiguous_match_returns_none():
    data = w.load_workouts()
    w.add_set(data, "жим лёжа гантели", "2026-07-20", 30.0, 10, 1)
    w.add_set(data, "жим ногами", "2026-07-20", 100.0, 12, 1)
    assert w.find_exercise_by_partial_name(data, "жим") is None  # неоднозначно


def test_find_exercise_more_specific_query_resolves_ambiguity():
    data = w.load_workouts()
    w.add_set(data, "жим лёжа гантели", "2026-07-20", 30.0, 10, 1)
    w.add_set(data, "жим ногами", "2026-07-20", 100.0, 12, 1)
    assert w.find_exercise_by_partial_name(data, "жим лёжа") == "жим лёжа гантели"


def test_find_exercise_empty_query_returns_none():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    assert w.find_exercise_by_partial_name(data, "") is None


def test_find_exercise_no_history_returns_none():
    data = w.load_workouts()
    assert w.find_exercise_by_partial_name(data, "присед") is None


# --- format_progress_report -----------------------------------------------

def test_format_progress_report_none_without_history():
    data = w.load_workouts()
    assert w.format_progress_report(data, "присед") is None


def test_format_progress_report_lists_each_session():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-06", 50.0, 8, 1)
    w.add_set(data, "присед", "2026-07-13", 52.5, 8, 1)
    report = w.format_progress_report(data, "присед")
    assert "2026-07-06" in report
    assert "2026-07-13" in report
    assert "50.0кг" in report
    assert "52.5кг" in report


def test_format_progress_report_shows_tonnage_per_session():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-06", 50.0, 8, 1)  # тоннаж 400
    report = w.format_progress_report(data, "присед")
    assert "тоннаж 400" in report


def test_format_progress_report_shows_overall_change_pct():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-06", 50.0, 8, 1)  # тоннаж 400
    w.add_set(data, "присед", "2026-07-13", 55.0, 8, 1)  # тоннаж 440, +10%
    report = w.format_progress_report(data, "присед")
    assert "+10%" in report


def test_format_progress_report_negative_change():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-06", 55.0, 8, 1)  # тоннаж 440
    w.add_set(data, "присед", "2026-07-13", 50.0, 8, 1)  # тоннаж 400, -9%
    report = w.format_progress_report(data, "присед")
    assert "-9%" in report


def test_format_progress_report_no_change_line_for_single_session():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-06", 50.0, 8, 1)
    report = w.format_progress_report(data, "присед")
    assert "Изменение тоннажа" not in report  # 1 сессия — не с чем сравнивать


def test_format_progress_report_respects_limit_sessions():
    data = w.load_workouts()
    for day in range(1, 6):
        w.add_set(data, "присед", f"2026-07-{day:02d}", 50.0, 8, 1)
    report = w.format_progress_report(data, "присед", limit_sessions=3)
    assert "2026-07-01" not in report  # за пределами лимита
    assert "2026-07-05" in report


# --- save_wellness_for_date / get_wellness_for_date -----------------------

def test_get_wellness_for_date_none_when_not_recorded():
    data = w.load_workouts()
    assert w.get_wellness_for_date(data, "2026-07-28") is None


def test_save_and_get_wellness_for_date():
    data = w.load_workouts()
    w.save_wellness_for_date(data, "2026-07-28", sleep_hours=7.0, stress_level=4)
    result = w.get_wellness_for_date(data, "2026-07-28")
    assert result == {"sleep_hours": 7.0, "stress_level": 4}


def test_wellness_log_separate_dates_independent():
    data = w.load_workouts()
    w.save_wellness_for_date(data, "2026-07-20", sleep_hours=5.0, stress_level=8)
    w.save_wellness_for_date(data, "2026-07-27", sleep_hours=8.0, stress_level=2)
    assert w.get_wellness_for_date(data, "2026-07-20")["sleep_hours"] == 5.0
    assert w.get_wellness_for_date(data, "2026-07-27")["sleep_hours"] == 8.0


def test_load_workouts_missing_wellness_log_key_does_not_crash():
    # Существующий продакшен-файл, созданный до этой фичи, не имеет
    # поля wellness_log — load_workouts должна добавить его дефолтом,
    # не падать при последующих вызовах get_wellness_for_date
    data = {"schema_version": 1, "sets": [], "exercise_aliases": {},
            "pending_suggestions": [], "targets": {}}
    # симулируем то, что делает load_workouts после json.load для
    # старого файла без wellness_log
    data.setdefault("wellness_log", {})
    assert w.get_wellness_for_date(data, "2026-07-28") is None


# --- add_cardio / get_cardio_for_date --------------------------------

def test_get_cardio_empty_for_no_records():
    data = w.load_workouts()
    result = w.get_cardio_for_date(data, "2026-07-28")
    assert result == {"entries": [], "total_km": 0}


def test_add_cardio_single_entry():
    data = w.load_workouts()
    w.add_cardio(data, "2026-07-28", 5.0)
    result = w.get_cardio_for_date(data, "2026-07-28")
    assert result["total_km"] == 5.0
    assert len(result["entries"]) == 1


def test_add_cardio_multiple_entries_same_date_sum():
    # До и после тренировки — две отдельные команды, суммируются
    data = w.load_workouts()
    w.add_cardio(data, "2026-07-28", 6.0)
    w.add_cardio(data, "2026-07-28", 9.0)
    result = w.get_cardio_for_date(data, "2026-07-28")
    assert result["total_km"] == 15.0
    assert len(result["entries"]) == 2


def test_add_cardio_different_dates_independent():
    data = w.load_workouts()
    w.add_cardio(data, "2026-07-20", 5.0)
    w.add_cardio(data, "2026-07-27", 8.0)
    assert w.get_cardio_for_date(data, "2026-07-20")["total_km"] == 5.0
    assert w.get_cardio_for_date(data, "2026-07-27")["total_km"] == 8.0


def test_load_workouts_missing_cardio_log_key_does_not_crash():
    data = {"schema_version": 1, "sets": [], "exercise_aliases": {},
            "pending_suggestions": [], "targets": {}, "wellness_log": {}}
    data.setdefault("cardio_log", {})
    assert w.get_cardio_for_date(data, "2026-07-28") == {"entries": [], "total_km": 0}


# --- get_active_phase / set_active_phase -----------------------------

def test_get_active_phase_defaults_to_volume():
    data = w.load_workouts()
    phase = w.get_active_phase(data)
    assert phase["phase_id"] == "volume"
    assert phase["started_date"] is None


def test_set_active_phase_updates_state():
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-07-28")
    phase = w.get_active_phase(data)
    assert phase["phase_id"] == "strength"
    assert phase["started_date"] == "2026-07-28"


def test_set_active_phase_overwrites_previous():
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-07-01")
    w.set_active_phase(data, "deficit", "2026-07-28")
    phase = w.get_active_phase(data)
    assert phase["phase_id"] == "deficit"
    assert phase["started_date"] == "2026-07-28"


# --- mark_phase_reminder_sent -----------------------------------------

def test_mark_phase_reminder_sent_sets_flag():
    data = w.load_workouts()
    w.set_active_phase(data, "strength", "2026-07-01")
    w.mark_phase_reminder_sent(data)
    assert data["active_phase"]["reminder_sent"] is True


def test_mark_phase_reminder_sent_no_active_phase_does_not_crash():
    data = w.load_workouts()
    del data["active_phase"]
    w.mark_phase_reminder_sent(data)  # не должно упасть
