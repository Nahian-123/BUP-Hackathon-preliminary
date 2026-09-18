from __future__ import annotations
from dotenv import load_dotenv; load_dotenv()

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import guardrails, llm, optimizer
from app.schemas import DirectiveInterpretation, HourlyPlanEntry, OptimizeRequest, OptimizeResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise")

app = FastAPI(title="GridWise Optimize-Energy Service")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy")
async def optimize_energy(request: Request):
    # Parse manually (not via FastAPI's automatic body model) so we control
    # the 400 vs 422 split exactly per Section 6.1 of the Problem Statement.
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Malformed JSON body."})

    try:
        req = OptimizeRequest.model_validate(payload)
    except ValidationError as e:
        return JSONResponse(
            status_code=422,
            content={"error": "Request failed schema validation.", "details": e.errors()},
        )

    try:
        # Build scenario-derived dicts once, up front.
        hours_list = [h.model_dump() for h in req.hours]
        battery_dict = req.battery.model_dump()

        # 1. LLM interpretation (mandatory LLM step).
        #    Battery context is passed in so the model can resolve
        #    percentage-based reserves to absolute kWh.
        raw_directives = llm.interpret_notes(req.operator_notes, battery_dict)

        # 2. Deterministic guardrail validation (untrusted -> trusted)
        directive_interpretation = guardrails.validate_all(
            raw_directives, len(req.operator_notes)
        )

        try:
            opt_result = optimizer.solve(hours_list, battery_dict, directive_interpretation)
        except optimizer.InfeasibleScenarioError as exc:
            logger.error("Infeasible scenario %s: %s", req.scenario_id, exc)
            return JSONResponse(
                status_code=422,
                content={"error": "Scenario is infeasible under the applied directives."},
            )

        response = OptimizeResponse(
            scenario_id=req.scenario_id,
            directive_interpretation=[
                DirectiveInterpretation(**d) for d in directive_interpretation
            ],
            hourly_plan=[HourlyPlanEntry(**p) for p in opt_result["hourly_plan"]],
            total_grid_kwh=opt_result["total_grid_kwh"],
            total_cost_bdt=opt_result["total_cost_bdt"],
            peak_grid_kwh=opt_result["peak_grid_kwh"],
            plan_summary=_summarize(directive_interpretation, opt_result),
        )
        return JSONResponse(status_code=200, content=response.model_dump())

    except Exception:
        # Controlled internal error — never leak stack traces or secrets.
        logger.exception("Unhandled error while optimizing scenario %s", req.scenario_id)
        return JSONResponse(status_code=500, content={"error": "Internal error while optimizing."})


def _summarize(directive_interpretation, opt_result) -> str:
    applied = [d for d in directive_interpretation if d["applies"]]
    if applied:
        types = ", ".join(sorted({d["directive_type"] for d in applied}))
        directive_note = f"Applied directives: {types}."
    else:
        directive_note = "No operator directives affected this schedule."
    return (
        f"{directive_note} Total grid cost {opt_result['total_cost_bdt']:.2f} BDT, "
        f"total grid draw {opt_result['total_grid_kwh']:.2f} kWh, "
        f"peak hourly grid draw {opt_result['peak_grid_kwh']:.2f} kWh."
    )
