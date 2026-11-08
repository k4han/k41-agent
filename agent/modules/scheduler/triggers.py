from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Literal

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from agent.shared.timezone import resolve_display_timezone

TriggerType = Literal["date", "relative", "interval", "cron"]
APSchedulerTriggerType = Literal["date", "interval", "cron"]
INTERVAL_TRIGGER_FIELDS = ("weeks", "days", "hours", "minutes", "seconds")
CRON_DISPLAY_FIELDS = ("minute", "hour", "day", "month", "day_of_week")
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_NUMERIC_WEEKDAY_RE = re.compile(r"(?P<start>\*|\d+)(?:-(?P<end>\d+))?(?:/(?P<step>\d+))?")
_INTERVAL_UNIT_SECONDS = (
    ("weeks", 7 * 86400),
    ("days", 86400),
    ("hours", 3600),
    ("minutes", 60),
    ("seconds", 1),
)


def _scheduler_timezone(scheduler: Any) -> Any:
    if timezone := getattr(scheduler, "timezone", None):
        return timezone
    _, display_zone = resolve_display_timezone()
    return display_zone


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

    if trigger_type == "cron" and normalized_args.get("day_of_week") not in (None, ""):
        normalized_args["day_of_week"] = _weekday_field_to_names(
            normalized_args["day_of_week"], sunday_first=True
        )

    return trigger_type, normalized_args


def _expand_numeric_weekday_part(part: str, sunday_first: bool) -> list[int] | None:
    """Expand a numeric weekday token into APScheduler weekday indexes (0 = Monday).

    Returns ``None`` when the token is not numeric (e.g. a weekday name), so it can be
    kept unchanged. ``sunday_first`` selects crontab numbering (0/7 = Sunday, 1 = Monday);
    otherwise APScheduler numbering (0 = Monday, 6 = Sunday) is assumed.
    """
    match = _NUMERIC_WEEKDAY_RE.fullmatch(part)
    if not match:
        return None
    max_value = 7 if sunday_first else 6
    if match["start"] == "*":
        start, end = 0, 6
    else:
        start = int(match["start"])
        end = int(match["end"]) if match["end"] is not None else start
    step = int(match["step"]) if match["step"] else 1
    if match["step"] and match["end"] is None and match["start"] != "*":
        end = max_value
    if not (0 <= start <= max_value and 0 <= end <= max_value) or start > end or step < 1:
        raise ValueError(
            f"Invalid day_of_week '{part}'. Use names (mon-sun) or numbers "
            f"0-{max_value}{' (0 or 7 = Sunday)' if sunday_first else ''}."
        )
    days = range(start, end + 1, step)
    if sunday_first:
        return sorted({(day - 1) % 7 for day in days})
    return sorted(set(days))


def _compress_weekdays(indexes: list[int]) -> list[str]:
    """Render sorted APScheduler weekday indexes as names, collapsing consecutive runs."""
    parts: list[str] = []
    run_start = previous = None
    for index in [*indexes, None]:
        if index is not None and previous is not None and index == previous + 1:
            previous = index
            continue
        if run_start is not None:
            if previous - run_start >= 2:
                parts.append(f"{WEEKDAY_NAMES[run_start]}-{WEEKDAY_NAMES[previous]}")
            else:
                parts.extend(WEEKDAY_NAMES[day] for day in range(run_start, previous + 1))
        run_start = previous = index
    return parts


def _weekday_field_to_names(value: Any, *, sunday_first: bool) -> str:
    """Rewrite numeric weekday tokens as names so the meaning is unambiguous."""
    text_value = str(value).strip()
    if text_value == "*":
        return text_value
    numeric_days: set[int] = set()
    named_parts: list[str] = []
    for raw_part in text_value.split(","):
        part = raw_part.strip()
        indexes = _expand_numeric_weekday_part(part, sunday_first)
        if indexes is None:
            named_parts.append(part.lower())
        else:
            numeric_days.update(indexes)
    return ",".join([*_compress_weekdays(sorted(numeric_days)), *named_parts])


def serialize_trigger_args(trigger: Any) -> dict[str, Any]:
    """Return the arguments that recreate ``trigger`` (cron fields, interval units, run date)."""
    if isinstance(trigger, CronTrigger):
        args: dict[str, Any] = {}
        for field in trigger.fields:
            if field.name in CRON_DISPLAY_FIELDS:
                value = str(field)
                if field.name == "day_of_week":
                    value = _weekday_field_to_names(value, sunday_first=False)
                args[field.name] = value
            elif not field.is_default:
                args[field.name] = str(field)
        return args
    if isinstance(trigger, IntervalTrigger):
        remaining = trigger.interval // timedelta(seconds=1)
        args = {}
        for name, seconds in _INTERVAL_UNIT_SECONDS:
            amount, remaining = divmod(remaining, seconds)
            if amount:
                args[name] = amount
        if trigger.interval.microseconds:
            args["seconds"] = args.get("seconds", 0) + trigger.interval.microseconds / 1_000_000
        return args
    if isinstance(trigger, DateTrigger):
        return {"run_date": trigger.run_date}
    return {}


def describe_trigger(trigger: Any) -> str:
    """Return a short, human-readable schedule such as ``cron 0 9 * * mon-fri``."""
    args = serialize_trigger_args(trigger)
    if isinstance(trigger, CronTrigger):
        description = "cron " + " ".join(str(args.get(name, "*")) for name in CRON_DISPLAY_FIELDS)
        constraints = [
            f"{field.name}={field}"
            for field in trigger.fields
            if field.name not in CRON_DISPLAY_FIELDS
            and str(field) != ("0" if field.name == "second" else "*")
        ]
        for name in ("start_date", "end_date", "jitter"):
            value = getattr(trigger, name)
            if value is not None:
                text = value.isoformat() if isinstance(value, datetime) else str(value)
                constraints.append(f"{name}={text}")
        if constraints:
            description += " (" + ", ".join(constraints) + ")"
        return description
    if isinstance(trigger, IntervalTrigger):
        units = " ".join(f"{value} {name}" for name, value in args.items())
        return f"every {units}" if units else "interval"
    if isinstance(trigger, DateTrigger):
        return f"once at {trigger.run_date.strftime('%Y-%m-%d %H:%M:%S %Z')}"
    return str(trigger)


__all__ = [
    "APSchedulerTriggerType",
    "CRON_DISPLAY_FIELDS",
    "INTERVAL_TRIGGER_FIELDS",
    "TriggerType",
    "describe_trigger",
    "normalize_trigger",
    "serialize_trigger_args",
]
