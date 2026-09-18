# GridWise — LLM-Assisted Smart Campus Energy Optimization

BUP CSE Fest 2026 Hackathon — Online Preliminary submission.

## Architecture

```
Energy Data + Operator Notes
        |
        v
  LLM Interpreter (app/llm.py)      <- language model reads operator_notes,
        |                              returns RAW candidate directives
        v
  Guardrail Validator (app/guardrails.py)  <- deterministic checks: allowed
        |                                     types, hour ranges, numeric
        |                                     ranges, note coverage/order
        v
  Math Optimizer (app/optimizer.py)  <- linear program (PuLP + CBC) that
        |                                minimizes grid cost subject to
        |                                demand, solar, battery, and
        |                                directive constraints
        v
  Final response (app/main.py)  <- directive_interpretation + hourly_plan
```

The LLM is only used in `app/llm.py`, to turn free-text operator notes into
structured candidate directives. Everything downstream of that
(`guardrails.py`, `optimizer.py`) is deterministic code — the LLM's output
is never trusted directly and never bypasses guardrail validation before
reaching the optimizer.

## Model / Provider

- Uses an **OpenAI-compatible chat completions API** via the `openai` Python
  SDK. Works with OpenAI directly, or any OpenAI-compatible endpoint
  (Groq, Together, DeepSeek, a local vLLM/Ollama server) by setting
  `LLM_BASE_URL`.
- Default model: `openai/gpt-oss-120b` (override with `LLM_MODEL`).
- If no API key is configured or the call fails for any reason, the service
  fails safe: every note is returned as `no_op` rather than crashing or
  inventing a directive.

## Environment variables

| Variable | Required | Meaning |
|---|---|---|
| `LLM_API_KEY` | Yes (for real interpretation) | API key for the LLM provider |
| `LLM_MODEL` | No | Model name, default `openai/gpt-oss-120b` |
| `LLM_BASE_URL` | No | Set only for a non-OpenAI OpenAI-compatible endpoint |
| `PORT` | No | Service port, default `8000` |

Copy `.env.example` to `.env` and fill in `LLM_API_KEY` for local runs.

## Local quickstart (clean environment)

```bash
git clone <your-repo-url>
cd gridwise
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export LLM_API_KEY=sk-...          # or `set` on Windows
export LLM_MODEL=gpt-4o-mini       # optional

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In another terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### Run the public sample cases

```bash
pip install requests
python test_public_samples.py path/to/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

This script checks schema shape, note coverage/order, energy balance, and
end-of-day battery neutrality against the public pack (not the hidden judge
set). It is a sanity check, not a scoring tool.

### Sample request

```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "DEMO-1",
    "operator_notes": ["Solar output will drop to about 20% from 1 PM to 3 PM."],
    "hours": [ ... 24 hourly entries ... ],
    "battery": {
      "capacity_kwh": 500,
      "initial_energy_kwh": 200,
      "minimum_energy_kwh": 50,
      "max_charge_kwh_per_hour": 100,
      "max_discharge_kwh_per_hour": 100
    }
  }'
```

## Optimizer / solver

Linear program built with [PuLP](https://coin-or.github.io/pulp/), solved
with the bundled CBC solver (`PULP_CBC_CMD`). Decision variables per hour:
grid import, solar used, battery charge, battery discharge, battery energy
level. Constraints implement Problem Statement Sections 5.3 and 9 exactly
(energy balance, effective-solar cap, battery bounds/rate limits, directive
constraints, end-of-day neutrality). Objective: minimize
`sum(grid_kwh[h] * tariff_bdt_per_kwh[h])`.

## Docker fallback

```bash
docker build -t gridwise:latest .
docker run -p 8000:8000 -e LLM_API_KEY=sk-... gridwise:latest
curl http://localhost:8000/health
```

Registry image: `docker.io/nahian-123/gridwise:v1`

## Known limitations

- LLM interpretation quality depends on the configured provider/model and
  its rate limits; the team is responsible for key/quota availability
  during judging, per the Participant Guide.
- If the LLM call fails or returns unparseable output, all notes for that
  request degrade to `no_op` (safe failure) rather than blocking the
  response.
- The optimizer assumes organizer-supplied scenarios are feasible (per
  Problem Statement Section 5.1); a genuinely infeasible scenario returns
  HTTP 422.

## Credits

- [FastAPI](https://fastapi.tiangolo.com/), [PuLP](https://coin-or.github.io/pulp/) (CBC solver), [OpenAI Python SDK](https://github.com/openai/openai-python).
