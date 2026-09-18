"""
LLM operator-note interpretation.

This is the ONLY place a language model is called. Its output is treated as
untrusted and is fully re-validated by app/guardrails.py before it ever
touches the optimizer — see Section 08 of the Problem Statement.

Provider is swappable via env vars so you can point this at OpenAI, an
OpenAI-compatible endpoint (Groq, Together, DeepSeek, a local vLLM/Ollama
server), or swap in the Anthropic SDK — whatever you have a key for.
Document whichever one you actually use in the README.
"""
from __future__ import annotations

import json
import os
from typing import List

MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
BASE_URL = os.environ.get("LLM_BASE_URL")  # None -> OpenAI's default endpoint
API_KEY = os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", ""))

_client = None
if API_KEY:
    try:
        from openai import OpenAI

        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL or None)
    except Exception:
        # Package missing or client failed to construct — fail safe, not crash.
        _client = None

SYSTEM_PROMPT = """You convert campus-operator energy notes into structured directives.

Supported directive types (use exactly these names):
- solar_reduction: {"hours":[...], "factor": number}   // factor = REMAINING usable fraction, e.g. an 80% reduction -> factor 0.2
- minimum_battery_reserve: {"hours":[...], "minimum_energy_kwh": number}
- no_charge_window: {"hours":[...]}
- no_discharge_window: {"hours":[...]}
- max_grid_window: {"hours":[...], "max_grid_kwh": number}
- no_op: no structured_adjustment (irrelevant / distractor notes)

Rules:
- Time windows are whole hours, END-EXCLUSIVE. "1 PM to 3 PM" -> hours [13,14]. "noon until 2 PM" -> [12,13].
- hours must be unique integers 0-23, ascending.  
- Only extract what a note actually implies. Do not invent numbers it doesn't state or imply.
- If a note does not affect the 24-hour energy schedule (e.g. unrelated campus announcements), it is no_op. 
- The same rule may be phrased many different ways (percentages, fractions, "roughly", clock times, "noon", "midnight"). Interpret meaning, not exact wording.
- PERCENTAGE RESERVES: If a note expresses a reserve as a percentage of battery capacity (e.g. "keep at least 50% of the battery capacity" or "half the battery stored"), compute the absolute kWh by multiplying the percentage by the battery's capacity_kwh from the BATTERY CONTEXT block below, and emit that absolute kWh as minimum_energy_kwh. Do NOT emit no_op just because the note gives a percentage instead of kWh.


Return ONLY a JSON array, one object per input note, in the same order, with this shape:
[{"note_index": 0, "directive_type": "...", "hours": [...], "value": number_or_null, "explanation": "short reason"}, ...]

"value" holds whichever single number the directive needs (factor / minimum_energy_kwh / max_grid_kwh), or null for
no_charge_window, no_discharge_window, and no_op. Output nothing except the JSON array — no prose, no markdown fences.
"""



def interpret_notes(notes: List[str], battery: dict | None = None) -> List[dict]:
    """
    Calls the LLM once with all notes and returns a list of RAW candidate
    directive dicts (one per note, in order). This raw output is untrusted
    and must be passed through guardrails.validate_all() before use.

    On any provider/parsing failure, fails SAFE: returns no_op for every
    note rather than crashing or inventing a directive (Section 08).
    """
    if _client is None:
        return [_no_op_fallback(i) for i in range(len(notes))]

    battery_context = ""
    if battery is not None:
        battery_context = (
            "\n\nBATTERY CONTEXT (use this to convert percentage-based reserves to absolute kWh):\n"
            f"- capacity_kwh = {battery.get('capacity_kwh')}\n"
            f"- minimum_energy_kwh = {battery.get('minimum_energy_kwh')}\n"
        )

    user_content = battery_context + json.dumps(
        [{"note_index": i, "text": n} for i, n in enumerate(notes)]
    )

    try:
        completion = _client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        raw = completion.choices[0].message.content.strip()
        raw = _strip_code_fence(raw)
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError("LLM did not return a JSON array")
        return parsed    
    
    except Exception:
        # SAFE FAILURE — never crash the service, never invent a directive.
        return [_no_op_fallback(i) for i in range(len(notes))]


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def _no_op_fallback(note_index: int) -> dict:
    return {
        "note_index": note_index,
        "directive_type": "no_op",
        "hours": [],
        "value": None,
        "explanation": "Fallback: interpretation unavailable, treated as no_op.",
    }
