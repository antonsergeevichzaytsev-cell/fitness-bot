"""Тесты для session.py — распознавание начала/конца тренировки,
жизненный цикл сессии, построение отчёта с трендами.
"""
import sys
from datetime import datetime, timedelta, timezone

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
    result = sess.end_session(data)
    assert result is None


def test_end_session_closes_active_session():
    data = w.load_workouts()
    sess.start_session(data)
    session_date = data["active_session"]["date"]
    w.add_set(data, "присед", session_date, 50.0, 8, 1)

    result = sess.end_session(data)
    assert result["exercises"] == ["присед"]
    assert result["date"] == session_date
    assert sess.is_session_active(data) is False


def test_end_session_returns_day_id():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    result = sess.end_session(data)
    assert result["day_id"] == "1"


def test_end_session_day_id_none_when_started_without_program():
    data = w.load_workouts()
    sess.start_session(data)  # без day_id — например, день отдыха
    result = sess.end_session(data)
    assert result["day_id"] is None


def test_end_session_collects_multiple_exercises():
    data = w.load_workouts()
    sess.start_session(data)
    session_date = data["active_session"]["date"]
    w.add_set(data, "присед", session_date, 50.0, 8, 1)
    w.add_set(data, "жим лёжа гантели", session_date, 30.0, 10, 1)

    result = sess.end_session(data)
    assert result["exercises"] == ["жим лёжа гантели", "присед"]  # отсортировано


def test_end_session_ignores_sets_from_other_dates():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-01", 50.0, 8, 1)  # старая запись, не сегодняшняя
    sess.start_session(data)

    result = sess.end_session(data)
    assert result["exercises"] == []  # ничего не записано именно в этой сессии


def test_end_session_returns_body_weight():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.set_body_weight(data, 121.5)
    result = sess.end_session(data)
    assert result["body_weight_kg"] == 121.5


def test_end_session_body_weight_none_when_not_set():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    result = sess.end_session(data)
    assert result["body_weight_kg"] is None


def test_end_session_returns_duration_minutes():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    data["active_session"]["started_ts"] = (
        datetime.now(timezone.utc) - timedelta(minutes=45)
    ).isoformat()
    result = sess.end_session(data)
    assert 44.5 <= result["duration_minutes"] <= 45.5


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
    # "тоннаж +N% к прошлой тренировке" — per-exercise тренд, отсутствует
    # без истории. "Итого тоннаж" (summary в конце отчёта) — другая
    # секция, показывается всегда, тест проверяет именно тренд-фразу.
    assert "к прошлой тренировке" not in report


def test_report_multiple_exercises():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    w.add_set(data, "жим лёжа гантели", "2026-07-28", 30.0, 10, 1)
    report = sess.build_session_report(data, ["жим лёжа гантели", "присед"], "2026-07-28")
    assert "присед" in report
    assert "жим лёжа гантели" in report


def test_report_shows_plan_vs_fact_when_day_id_given():
    data = w.load_workouts()
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.add_set(data, normalized, "2026-07-28", 47.5, 9, 1)
    report = sess.build_session_report(data, [normalized], "2026-07-28", day_id="1")
    assert "план:" in report
    assert "8-10" in report
    assert "45-50кг" in report
    assert "\u2705" in report  # 47.5кг/9 повторов — в плане


def test_report_no_plan_vs_fact_without_day_id():
    # Обратная совместимость: без day_id (например, тренировка не по
    # расписанию) — только тренд, как было раньше, без 'план:'
    data = w.load_workouts()
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.add_set(data, normalized, "2026-07-28", 47.5, 9, 1)
    report = sess.build_session_report(data, [normalized], "2026-07-28")
    assert "план:" not in report


def test_report_falls_back_to_trend_when_exercise_not_in_day_plan():
    # Упражнение записано текстом отдельно (не по плану дня 1) —
    # plan_ex не найден, отчёт не падает, просто без план/факт секции
    data = w.load_workouts()
    w.add_set(data, "совсем другое упражнение", "2026-07-28", 10.0, 5, 1)
    report = sess.build_session_report(data, ["совсем другое упражнение"], "2026-07-28", day_id="1")
    assert "совсем другое упражнение" in report
    assert "план:" not in report


# --- build_session_report: тоннаж и калории -----------------------------

def test_report_shows_total_tonnage():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    w.add_set(data, "жим лёжа", "2026-07-28", 30.0, 10, 1)
    report = sess.build_session_report(data, ["присед", "жим лёжа"], "2026-07-28")
    # 50*8 + 30*10 = 400 + 300 = 700
    assert "Итого тоннаж" in report
    assert "700" in report


def test_report_shows_calories_when_weight_and_duration_given():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(
        data, ["присед"], "2026-07-28", body_weight_kg=121.0, duration_minutes=60.0
    )
    assert "Оценка калорий" in report
    # 60 * (3.5*3.5*121)/200 = 444.675 -> round 445
    assert "445" in report


def test_report_no_calories_without_weight():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28", duration_minutes=60.0)
    assert "Оценка калорий" not in report


def test_report_no_calories_without_duration():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28", body_weight_kg=121.0)
    assert "Оценка калорий" not in report


def test_report_tonnage_always_shown_even_without_calories():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    report = sess.build_session_report(data, ["присед"], "2026-07-28")
    assert "Итого тоннаж" in report


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


def test_current_exercise_info_applies_confirmed_target():
    # Регрессия на находку 28.07.2026: target прогрессии сохранялся
    # через w.set_target, но НИКОГДА не применялся в пошаговом флоу —
    # current_exercise_info продолжала бы отдавать старый вес из
    # training_program.json. Этот тест защищает фикс.
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)
    ex, set_num = sess.current_exercise_info(data)
    assert ex["weight_min_kg"] == 52.5
    assert ex["weight_max_kg"] == 52.5


def test_current_exercise_info_no_target_uses_static_plan():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    ex, set_num = sess.current_exercise_info(data)
    assert ex["weight_min_kg"] == 45  # без target — оригинальный план
    assert ex["weight_max_kg"] == 50


def test_current_exercise_info_target_does_not_mutate_program_json():
    # Копия dict, не мутация shared training_program.json в памяти —
    # иначе target одного упражнения "протёк" бы в program.load_program()
    # для всех последующих вызовов в этом же процессе
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)
    sess.current_exercise_info(data)  # применяет target

    fresh_ex = prog.get_exercise("1", 1)  # прямой доступ к статичному плану
    assert fresh_ex["weight_min_kg"] == 45  # не изменилось


def test_current_exercise_info_replacement_override_takes_priority_over_target():
    # Приоритет: exercise_overrides (ручная замена тренажёра) важнее
    # target (автопрогрессия веса того же упражнения) — замена меняет
    # упражнение целиком, target бессмыслен для другого упражнения
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    normalized = w.normalize_exercise_name("Vertical Traction (тяга сверху к груди)", {})
    w.set_target(data, normalized, 52.5, 8)

    replacement = {
        "name": "Cable Row замена", "machine": "Кроссовер", "sets": 4,
        "reps_min": 8, "reps_max": 10, "weight_min_kg": 30, "weight_max_kg": 35,
        "tempo": "2-1-2-0", "rest_sec": 90, "order": 1, "per_side": False,
    }
    sess.apply_replacement(data, 1, replacement)

    ex, set_num = sess.current_exercise_info(data)
    assert ex["name"] == "Cable Row замена"  # замена, не оригинал с target


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


# --- rest_timer_expired / mark_reminder_sent -----------------------------
# Проверяется Cron Trigger'ом раз в минуту (timer.py) — критично: не
# должен слать напоминание повторно, должен корректно сбрасываться на
# новый подход.

def test_rest_timer_not_expired_before_rest_sec():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")  # упражнение 1: rest_sec=90
    sess.advance_position(data, weight_kg=47.5, reps=10)
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert sess.rest_timer_expired(data, now=soon) is False


def test_rest_timer_expired_after_rest_sec():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    later = datetime.now(timezone.utc) + timedelta(seconds=100)
    assert sess.rest_timer_expired(data, now=later) is True


def test_rest_timer_no_active_session():
    data = w.load_workouts()
    assert sess.rest_timer_expired(data) is False


def test_rest_timer_no_resting_until_set():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")  # начал, но ещё ни одного подхода
    assert sess.rest_timer_expired(data) is False


def test_mark_reminder_sent_prevents_repeat():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    later = datetime.now(timezone.utc) + timedelta(seconds=100)
    assert sess.rest_timer_expired(data, now=later) is True
    sess.mark_reminder_sent(data)
    assert sess.rest_timer_expired(data, now=later) is False


def test_new_set_resets_reminder_sent_flag():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    sess.mark_reminder_sent(data)
    assert data["active_session"]["reminder_sent"] is True
    sess.advance_position(data, weight_kg=47.5, reps=10)  # новый подход
    assert data["active_session"]["reminder_sent"] is False


def test_mark_reminder_sent_no_active_session_does_not_crash():
    data = w.load_workouts()
    sess.mark_reminder_sent(data)  # не должно упасть без активной сессии


# --- is_extend_rest_request / extract_extend_seconds -----------------

def test_is_extend_rest_request_matches_keywords():
    for text in ["продли отдых", "продли на 30", "ещё минуту", "ещё секунд 20",
                 "устал", "нужно больше времени", "добавь времени"]:
        assert sess.is_extend_rest_request(text) is True, f"failed on {text!r}"


def test_is_extend_rest_request_false_for_workout_log():
    assert sess.is_extend_rest_request("присед 50 на 8") is False


def test_is_extend_rest_request_false_for_set_confirmation():
    assert sess.is_extend_rest_request("взял") is False


def test_extract_extend_seconds_plain_number():
    assert sess.extract_extend_seconds("продли на 30") == 30
    assert sess.extract_extend_seconds("продли на 45") == 45


def test_extract_extend_seconds_explicit_seconds():
    assert sess.extract_extend_seconds("ещё 20 сек") == 20


def test_extract_extend_seconds_one_minute_no_digit():
    assert sess.extract_extend_seconds("ещё минуту") == 60


def test_extract_extend_seconds_multiple_minutes():
    assert sess.extract_extend_seconds("ещё 2 минуты") == 120


def test_extract_extend_seconds_default_when_no_number():
    assert sess.extract_extend_seconds("устал") == 30


def test_extract_extend_seconds_custom_default():
    assert sess.extract_extend_seconds("устал", default_sec=60) == 60


# --- extend_rest -----------------------------------------------------

def test_extend_rest_no_active_session_returns_none():
    data = w.load_workouts()
    assert sess.extend_rest(data, 30) is None


def test_extend_rest_no_resting_until_returns_none():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")  # начал, но ещё ни одного подхода
    assert sess.extend_rest(data, 30) is None


def test_extend_rest_adds_seconds_before_expiry():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    before = datetime.fromisoformat(data["active_session"]["resting_until"])
    sess.extend_rest(data, 30)
    after = datetime.fromisoformat(data["active_session"]["resting_until"])
    assert abs((after - before).total_seconds() - 30) < 1


def test_extend_rest_after_expiry_extends_from_now_not_from_past():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    # искусственно истекаем таймер в прошлое
    data["active_session"]["resting_until"] = (
        datetime.now(timezone.utc) - timedelta(seconds=50)
    ).isoformat()
    new_val = sess.extend_rest(data, 30)
    new_dt = datetime.fromisoformat(new_val)
    now = datetime.now(timezone.utc)
    # новое время должно быть ~30 сек ОТ СЕЙЧАС, не ~-20 сек (что было бы,
    # если бы продление считалось от старого resting_until в прошлом)
    assert abs((new_dt - now).total_seconds() - 30) < 2


def test_extend_rest_resets_reminder_sent():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    sess.mark_reminder_sent(data)
    assert data["active_session"]["reminder_sent"] is True
    sess.extend_rest(data, 30)
    assert data["active_session"]["reminder_sent"] is False


# --- is_replace_exercise_request / is_skip_request / is_undo_request ----

def test_is_replace_exercise_request_matches_keywords():
    for text in ["замени упражнение", "заменить", "тренажёр занят",
                 "не работает", "сломан"]:
        assert sess.is_replace_exercise_request(text) is True, f"failed on {text!r}"


def test_is_replace_exercise_request_false_for_workout_log():
    assert sess.is_replace_exercise_request("присед 50 на 8") is False


def test_is_skip_request_matches_keywords():
    for text in ["пропусти", "скип", "не буду делать"]:
        assert sess.is_skip_request(text) is True, f"failed on {text!r}"


def test_is_skip_request_false_for_workout_log():
    assert sess.is_skip_request("присед 50 на 8") is False


def test_is_undo_request_matches_keywords():
    for text in ["отмени", "отмена", "ошибся", "не то записал"]:
        assert sess.is_undo_request(text) is True, f"failed on {text!r}"


def test_is_undo_request_false_for_workout_log():
    assert sess.is_undo_request("присед 50 на 8") is False


# --- is_progress_request / extract_progress_query ------------------------

def test_is_progress_request_matches_keywords():
    for text in ["покажи прогресс по жиму", "прогресс по приседу",
                 "как дела с жимом лёжа", "статистика по приседу"]:
        assert sess.is_progress_request(text) is True, f"failed on {text!r}"


def test_is_progress_request_false_for_workout_log():
    assert sess.is_progress_request("присед 50 на 8") is False


def test_extract_progress_query_strips_keyword_and_preposition():
    assert sess.extract_progress_query("покажи прогресс по жиму") == "жиму"
    assert sess.extract_progress_query("прогресс по приседу") == "приседу"


def test_extract_progress_query_with_preposition_c():
    assert sess.extract_progress_query("как дела с жимом лёжа") == "жимом лёжа"


def test_extract_progress_query_with_preposition_co():
    assert sess.extract_progress_query("покажи прогресс со штангой") == "штангой"


def test_extract_progress_query_empty_when_nothing_after_keyword():
    assert sess.extract_progress_query("покажи прогресс") == ""


def test_extract_progress_query_no_keyword_returns_empty():
    assert sess.extract_progress_query("присед 50 на 8") == ""


# --- skip_exercise -----------------------------------------------------

def test_skip_exercise_no_active_session_returns_none():
    data = w.load_workouts()
    skipped, next_ex = sess.skip_exercise(data)
    assert skipped is None and next_ex is None


def test_skip_exercise_moves_to_next_without_recording():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    skipped, next_ex = sess.skip_exercise(data)
    assert skipped["name"] == "Vertical Traction (тяга сверху к груди)"
    assert next_ex["name"] == "Low Row нейтральным хватом"
    assert data["sets"] == []  # ничего не записано
    assert data["active_session"]["current_exercise_order"] == 2
    assert data["active_session"]["current_set_number"] == 1


def test_skip_exercise_last_in_day_returns_none_next():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    last_order = prog.get_day_plan("1")["exercises"][-1]["order"]
    data["active_session"]["current_exercise_order"] = last_order
    skipped, next_ex = sess.skip_exercise(data)
    assert next_ex is None
    assert data["active_session"]["current_exercise_order"] is None


def test_skip_exercise_clears_rest_timer():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)  # выставляет resting_until
    sess.skip_exercise(data)
    assert data["active_session"]["resting_until"] is None


# --- undo_last_set -----------------------------------------------------

def test_undo_last_set_empty_sets_returns_none():
    data = w.load_workouts()
    assert sess.undo_last_set(data) is None


def test_undo_last_set_removes_most_recent():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    sess.advance_position(data, weight_kg=47.5, reps=10)
    assert len(data["sets"]) == 2
    undone = sess.undo_last_set(data)
    assert undone["set_number"] == 2
    assert len(data["sets"]) == 1


def test_undo_last_set_rolls_back_current_set_number():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    sess.advance_position(data, weight_kg=47.5, reps=10)
    sess.advance_position(data, weight_kg=47.5, reps=10)
    assert data["active_session"]["current_set_number"] == 3
    sess.undo_last_set(data)
    assert data["active_session"]["current_set_number"] == 2


def test_undo_last_set_without_active_session_still_removes():
    data = w.load_workouts()
    w.add_set(data, "присед", "2026-07-28", 50.0, 8, 1)
    undone = sess.undo_last_set(data)
    assert undone["exercise"] == "присед"
    assert data["sets"] == []


# --- apply_replacement / current_exercise_info override ------------------

def test_apply_replacement_no_active_session_returns_false():
    data = w.load_workouts()
    assert sess.apply_replacement(data, 1, {"name": "test"}) is False


def test_apply_replacement_overrides_current_exercise_info():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    replacement = {
        "name": "Cable Row замена", "machine": "Кроссовер", "sets": 4,
        "reps_min": 8, "reps_max": 10, "weight_min_kg": 40, "weight_max_kg": 45,
        "tempo": "2-1-2-0", "rest_sec": 90, "order": 1, "per_side": False,
    }
    sess.apply_replacement(data, 1, replacement)
    ex, set_num = sess.current_exercise_info(data)
    assert ex["name"] == "Cable Row замена"


def test_apply_replacement_only_affects_specific_order():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    replacement = {
        "name": "Cable Row замена", "machine": "Кроссовер", "sets": 4,
        "reps_min": 8, "reps_max": 10, "weight_min_kg": 40, "weight_max_kg": 45,
        "tempo": "2-1-2-0", "rest_sec": 90, "order": 1, "per_side": False,
    }
    sess.apply_replacement(data, 1, replacement)
    # переходим на order=2 — там замены нет, должен вернуться обычный план
    data["active_session"]["current_exercise_order"] = 2
    ex, set_num = sess.current_exercise_info(data)
    assert ex["name"] == "Low Row нейтральным хватом"


def test_apply_replacement_advance_position_records_replacement_name():
    data = w.load_workouts()
    sess.start_session(data, day_id="1")
    replacement = {
        "name": "Cable Row замена", "machine": "Кроссовер", "sets": 4,
        "reps_min": 8, "reps_max": 10, "weight_min_kg": 40, "weight_max_kg": 45,
        "tempo": "2-1-2-0", "rest_sec": 90, "order": 1, "per_side": False,
    }
    sess.apply_replacement(data, 1, replacement)
    result = sess.advance_position(data, weight_kg=42.5, reps=9)
    # recorded_exercise возвращает имя как в плане (не нормализованное) —
    # нормализация происходит внутри add_set для data['sets'], не для
    # возвращаемого значения advance_position
    assert result["recorded_exercise"] == "Cable Row замена"
    assert data["sets"][-1]["exercise"] == "cable row замена"


