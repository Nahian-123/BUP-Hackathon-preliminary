"""
Deterministic validation of raw LLM directive output.

Nothing from llm.py reaches the optimizer without passing through here.
Any entry that fails validation is forced to no_op rather than silently
trusted or used to crash the request (Section 08, SAFE FAILURE).
"""
from __future__ import annotations

from typing import List

ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _clean_hours(hours) -> List[int]:
    if not isinstance(hours, list):
        return []
    try:
        ints = sorted({int(h) for h in hours if 0 <= int(h) <= 23})
    except (TypeError, ValueError):
        return []
    return ints


def _force_no_op(note_index: int, reason: str) -> dict:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": f"Guardrail: {reason}",
    }


def validate_entry(raw: dict, expected_index: int) -> dict:
    """Validate and normalize a single raw LLM directive entry."""
    if not isinstance(raw, dict):
        return _force_no_op(expected_index, "malformed entry")

    note_index = raw.get("note_index", expected_index)
    if note_index != expected_index:
        # Note mapping must be exact; trust position over a wrong LLM index.
        note_index = expected_index

    dtype = raw.get("directive_type")
    if dtype not in ALLOWED_TYPES:
        return _force_no_op(note_index, "unsupported directive_type")

    if dtype == "no_op":
        return {
            "note_index": note_index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": raw.get("explanation", "Not relevant to the energy schedule."),
        }

    hours = _clean_hours(raw.get("hours"))
    if not hours:
        return _force_no_op(note_index, "no valid hours for a directive that requires them")

    value = raw.get("value")

    if dtype == "solar_reduction":
        try:
            factor = float(value)
        except (TypeError, ValueError):
            return _force_no_op(note_index, "solar_reduction missing numeric factor")
        factor = min(1.0, max(0.0, factor))
        adjustment = {"hours": hours, "factor": factor}

    elif dtype == "minimum_battery_reserve":
        try:
            reserve = float(value)
        except (TypeError, ValueError):
            return _force_no_op(note_index, "minimum_battery_reserve missing numeric value")
        if reserve < 0:
            return _force_no_op(note_index, "negative reserve value")
        adjustment = {"hours": hours, "minimum_energy_kwh": reserve}

    elif dtype == "max_grid_window":
        try:
            cap = float(value)
        except (TypeError, ValueError):
            return _force_no_op(note_index, "max_grid_window missing numeric cap")
        if cap < 0:
            return _force_no_op(note_index, "negative grid cap")
        adjustment = {"hours": hours, "max_grid_kwh": cap}

    elif dtype in ("no_charge_window", "no_discharge_window"):
        adjustment = {"hours": hours}

    else:  # pragma: no cover - unreachable, ALLOWED_TYPES already checked
        return _force_no_op(note_index, "unhandled directive_type")

    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": dtype,
        "structured_adjustment": adjustment,
        "explanation": str(raw.get("explanation", ""))[:300],
    }


def validate_all(raw_entries: List[dict], note_count: int) -> List[dict]:
    """
    Guarantees exactly `note_count` entries, in order, note_index 0..N-1,
    each individually guardrailed. Missing entries are backfilled as no_op
    so coverage is never short (Section 11.1 / Metric: interpretation coverage).
    """
    by_index = {}
    for i, raw in enumerate(raw_entries[:note_count]):
        entry = validate_entry(raw, i)
        by_index[entry["note_index"]] = entry

    return [
        by_index.get(i, _force_no_op(i, "missing from LLM output"))
        for i in range(note_count)
    ]
