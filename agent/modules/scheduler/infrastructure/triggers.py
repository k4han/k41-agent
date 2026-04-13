from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

TriggerType = Literal["date", "relative", "interval", "cron"]
APSchedulerTriggerType = Literal["date", "interval", "cron"]
INTERVAL_TRIGGER_FIELDS = ("weeks", "days", "hours", "minutes", "seconds")


def _scheduler_timezone(scheduler: Any) -> Any:
    return getattr(scheduler, "timezone", None) or datetime.now().astimezone().tzinfo


def _scheduler_now(scheduler: Any) -> datetime:
    scheduler_timezone = _scheduler_timezone(scheduler)
    return datetime.now(scheduler_timezone)


def _parse_run_date(value: Any, scheduler_timezone: Any) -> datetime:
    if isinstance(value, datetime):
        run_date = value
    else:
        value_str = str(value).strip()
        try:
            run_date = datetime.strptime(value_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                run_date = datetime.fromisoformat(value_str)
            except ValueError as exc:
                raise ValueError(
                    f"Invalid run_date format '{value_str}'. Expected YYYY-MM-DD HH:MM:SS."
                ) from exc

    if run_date.tzinfo is None:
        return run_date.replace(tzinfo=scheduler_timezone)
    return run_date.astimezone(scheduler_timezone)


def _coerce_non_negative_number(value: Any, field_name: str, trigger_label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{trigger_label} trigger field '{field_name}' must be a number."
        ) from exc
    if number < 0:
        raise ValueError(
            f"{trigger_label} trigger field '{field_name}' cannot be negative."
        )
    return number


def _build_duration_delta(
    trigger_args: dict[str, Any],
    trigger_label: str,
) -> timedelta:
    values = {
        field: _coerce_non_negative_number(
            trigger_args.get(field, 0) or 0,
            field,
            trigger_label,
        )
        for field in INTERVAL_TRIGGER_FIELDS
    }
    delta = timedelta(**values)
    if delta.total_seconds() <= 0:
        raise ValueError(
            f"{trigger_label} trigger requires at least one positive value "
            "(weeks/days/hours/minutes/seconds)."
        )
    return delta


def normalize_trigger(
    trigger_type: TriggerType,
    trigger_args: dict[str, Any],
    scheduler: Any,
    now: datetime | None = None,
) -> tuple[APSchedulerTriggerType, dict[str, Any]]:
    scheduler_timezone = _scheduler_timezone(scheduler)
    now = now or _scheduler_now(scheduler)
    if now.tzinfo is None:
        now = now.replace(tzinfo=scheduler_timezone)
    else:
        now = now.astimezone(scheduler_timezone)

    if trigger_type == "relative":
        delta = _build_duration_delta(trigger_args, "Relative")
        return "date", {"run_date": now + delta}

    normalized_args = dict(trigger_args)
    if trigger_type == "date":
        run_date_raw = normalized_args.get("run_date")
        if not run_date_raw:
            raise ValueError("'run_date' is required for date trigger.")

        run_date = _parse_run_date(run_date_raw, scheduler_timezone)
        if run_date <= now:
            now_text = now.strftime("%Y-%m-%d %H:%M:%S %Z")
            raise ValueError(
                f"run_date '{run_date_raw}' is in the past. "
                f"Current scheduler time is {now_text}. Use a future date/time "
                "or the 'relative' trigger for delays."
            )
        normalized_args["run_date"] = run_date

    if trigger_type == "interval":
        _build_duration_delta(normalized_args, "Interval")

    return trigger_type, normalized_args


__all__ = [
    "APSchedulerTriggerType",
    "INTERVAL_TRIGGER_FIELDS",
    "TriggerType",
    "normalize_trigger",
]
