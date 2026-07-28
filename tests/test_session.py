"""Тесты для session.py — распознавание начала/конца тренировки,
жизненный цикл сессии, построение отчёта с трендами.
"""
import sys

sys.path.insert(0, "..")
import program as prog
import session as sess
import workouts as w


# --- is_session_start / is_session_end ------------------------------

def test_is_session_start_matches_keywords():
    for text in ["начал", "Начинаю тренировку", "старт", "погнали", "поехали тренироваться"]:
        assert sess.is_session_start(text) is True, f"failed on {text!r}"


def test_is_session_start_false_for_workout_log():
    assert sess.is_session_start("жим лежа 30 на 10") is False


def test_is_session_start_case_insensitive():
    assert sess.is_session_start("НАЧАЛ") is True


def test_is_session_end_matches_keywords():
    for text in ["закончил", "конец тренировки", "финиш", "всё, закончили", "готово с тренировкой"]:
        assert sess.is_session_end(text) is True, f"failed on {text!r}"


def test_is_session_end_false_for_workout_log():
    assert sess.is_session_end("присед 50 на 8") is False


# --- start_session / end_session / is_session_active -------------------

def test_start_session_returns_true_first_time():
    data = w.load_workouts()
    assert sess.start_session(data) is True
    assert sess.is_session_active(data) is True


def test_start_session_idempotent():
    data = w.load_workouts()
    sess.start_session(data)
    assert sess.start_session(data) is False  # уже открыта
    assert sess.is_session_active(data) is True


def test_end_session_without_start_returns_none():
    data = w.load_workouts()
    exercises, date = sess.end_session(data)
    assert exercises is None
    assert date is None


def test_end_session_closes_active_session():
    data = w.load_workouts()
    sess.start_session(data)
    session_date = data["active_session"]["date"]
    w.add_set(data, "присед", session_date, 50.0, 8, 1)

    exercises, date = sess.end_session(data)
    assert exercises == ["присед"]
    assert date == session_date
    assert sess.is_session_active(data) is False


def test_end_session_collects_multiple_exercises():
    data = w.load_workouts()
    sess.start_session(data)
    session_date = data["active_session"]["date"]
    w.add_set(data, "присед", session_date, 50.0, 8, 1)
    w.add_set(data, "жим лёжа гантели", session_date, 30.0, 10, 1)

    exercises, _ = sess.end_session(data)
    assert exercises == ["жим лёжа гантели", "присед"]  # отсортировано


def test_end_session_ignores_sets_from_other_dates():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-01", 50.0, 8, 1)  # старая запись, не сегодняшняя
    sess.start_session(data)
    session_date = data["active_session"]["date"]

    exercises, _ = sess.end_session(data)
    assert exercises == []  # ничего не записано именно в этой сессии


# --- build_session_report -----------------------------------------------

def test_report_empty_session():
    data = w.load_workouts()
    report = sess.build_session_report(data, [], "2026-07-28")
    assert "не записано" in report


def test_report_shows_today_sets():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28")
    assert "присед" in report
    assert "50.0" in report


def test_report_shows_trend_when_prior_session_exists():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-20", 50.0, 8, 1)
    w.add_set(data, "присед", "2026-07-28", 52.5, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28")
    assert "тоннаж" in report
    assert "+5%" in report  # (52.5*8 - 50*8)/(50*8)*100 = 5%


def test_report_no_trend_line_when_no_prior_session():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28")
    assert "тоннаж" not in report  # нет истории — нет тренда


def test_report_multiple_exercises():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    w.add_set(data, "жим лёжа гантели", "2026-07-28", 30.0, 10, 1)
    report = sess.build_session_report(data, ["жим лёжа гантели", "присед"], "2026-07-28")
    assert "присед" in report
    assert "жим лёжа гантели" in report


# --- _format_trend ---------------------------------------------------

def test_format_trend_positive():
    today = [{"weight_kg": 55.0, "reps": 8}]
    prior = [{"weight_kg": 50.0, "reps": 8}]
    result = sess._format_trend(today, prior)
    assert "+10%" in result
    assert "\U0001f4c8" in result


def test_format_trend_negative():
    today = [{"weight_kg": 45.0, "reps": 8}]
    prior = [{"weight_kg": 50.0, "reps": 8}]
    result = sess._format_trend(today, prior)
    assert "-10%" in result
    assert "\U0001f4c9" in result


def test_format_trend_unchanged():
    today = [{"weight_kg": 50.0, "reps": 8}]
    prior = [{"weight_kg": 50.0, "reps": 8}]
    result = sess._format_trend(today, prior)
    assert "как в прошлый раз" in result


def test_format_trend_first_time_with_weight():
    today = [{"weight_kg": 50.0, "reps": 8}]
    prior = [{"weight_kg": None, "reps": 8}]  # прошлый раз был без веса
    result = sess._format_trend(today, prior)
    assert "первая тренировка с весом" in result


# --- is_set_confirmation ---------------------------------------------

def test_set_confirmation_matches_short_phrases():
    for text in ["взял", "готово", "сделал", "есть"]:
        assert sess.is_set_confirmation(text) is True, f"failed on {text!r}"


def test_set_confirmation_false_for_long_workout_log():
    # 'сделал' встречается, но это полноценная запись подхода с деталями
    # (вес/повторы/заметка) — не короткое подтверждение, разная обработка
    assert sess.is_set_confirmation("сделал присед 50 на 8, тяжело пошёл") is False


def test_set_confirmation_false_unrelated_text():
    assert sess.is_set_confirmation("жим лежа 30 на 10") is False


def test_set_confirmation_case_insensitive():
    assert sess.is_set_confirmation("ВЗЯЛ") is True


# --- current_exercise_info -----------------------------------------------

def test_current_exercise_info_no_active_session():
    data = w.load_workouts()
    ex, set_num = sess.current_exercise_info(data)
    assert ex is None and set_num is None


def test_current_exercise_info_session_without_day_id():
    data = w.load_workouts()
    sess.start_session(data)  # без day_id (например, начал в день отдыха)
    ex, set_num = sess.current_exercise_info(data)
    assert ex is None and set_num is None


def test_current_exercise_info_returns_first_exercise():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    ex, set_num = sess.current_exercise_info(data)
    assert ex["name"] == "Vertical Traction (тяга сверху к груди)"
    assert set_num == 1


def test_current_exercise_info_none_after_day_exhausted():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    session = data["active_session"]
    session["current_exercise_order"] = 999  # искусственно за пределами дня
    ex, set_num = sess.current_exercise_info(data)
    assert ex is None and set_num is None


# --- advance_position -----------------------------------------------------

def test_advance_position_no_active_session_records_nothing():
    data = w.load_workouts()
    result = sess.advance_position(data, weight_kg=30.0, reps=10)
    assert result["recorded_exercise"] is None
    assert data["sets"] == []


def test_advance_position_records_set_and_stays_on_same_exercise():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")  # упражнение 1: 4 подхода
    result = sess.advance_position(data, weight_kg=47.5, reps=10)
    assert result["recorded_exercise"] == "Vertical Traction (тяга сверху к груди)"
    assert result["day_complete"] is False
    assert result["next_set_number"] == 2
    assert len(data["sets"]) == 1
    assert data["sets"][0]["weight_kg"] == 47.5


def test_advance_position_moves_to_next_exercise_after_last_set():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    for _ in range(4):  # упражнение 1 имеет ровно 4 подхода
        result = sess.advance_position(data, weight_kg=47.5, reps=10)
    assert result["next_exercise"]["name"] == "Low Row нейтральным хватом"
    assert result["next_set_number"] == 1
    assert result["day_complete"] is False


def test_advance_position_day_complete_on_very_last_set():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    total_sets = sum(ex["sets"] for ex in prog.get_day_plan("1")["exercises"])
    for _ in range(total_sets):
        result = sess.advance_position(data, weight_kg=30.0, reps=10)
    assert result["day_complete"] is True
    assert result["next_exercise"] is None
    assert len(data["sets"]) == total_sets


def test_advance_position_uses_rest_sec_from_recorded_exercise():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")  # упражнение 1: rest_sec=90
    result = sess.advance_position(data, weight_kg=47.5, reps=10)
    assert result["rest_sec"] == 90


def test_advance_position_passes_rpe_and_note_through():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10, rpe=8, note="тяжело")
    assert data["sets"][0]["rpe"] == 8
    assert data["sets"][0]["note"] == "тяжело"

