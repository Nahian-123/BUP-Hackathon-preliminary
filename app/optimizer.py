"""
GridWise 24-hour cost-minimizing scheduler, built as a linear program.

Every constraint here maps 1:1 to a rule in Problem Statement Section 09
and Section 5.3. Using an LP solver (CBC via PuLP) guarantees a
cost-optimal, provably feasible schedule instead of a hand-rolled
heuristic that only handles the cases you thought of.
"""
from __future__ import annotations

from typing import List

import pulp

TOL = 1e-6


class InfeasibleScenarioError(Exception):
    pass


def solve(hours: list, battery: dict, directives: List[dict]) -> dict:
    """
    hours: list of 24 dicts with hour, demand_kwh, solar_kwh, tariff_bdt_per_kwh
           (already sorted 0..23)
    battery: dict with capacity_kwh, initial_energy_kwh, minimum_energy_kwh,
             max_charge_kwh_per_hour, max_discharge_kwh_per_hour
    directives: guardrailed entries from guardrails.validate_all() where
                applies is True (no_op entries should be filtered out
                before calling this, but it's harmless if not)

    Returns a dict with hourly_plan, total_grid_kwh, total_cost_bdt,
    peak_grid_kwh — ready to drop into the response schema.
    """
    n = 24
    demand = [h["demand_kwh"] for h in hours]
    base_solar = [h["solar_kwh"] for h in hours]
    tariff = [h["tariff_bdt_per_kwh"] for h in hours]

    effective_solar = list(base_solar)
    min_reserve = [battery["minimum_energy_kwh"]] * n
    no_charge_hours = set()
    no_discharge_hours = set()
    max_grid = [None] * n  # None == unconstrained

    for d in directives:
        if not d.get("applies"):
            continue
        dtype = d["directive_type"]
        adj = d["structured_adjustment"] or {}
        for h in adj.get("hours", []):
            if dtype == "solar_reduction":
                effective_solar[h] = base_solar[h] * adj["factor"]
            elif dtype == "minimum_battery_reserve":
                min_reserve[h] = max(min_reserve[h], adj["minimum_energy_kwh"])
            elif dtype == "no_charge_window":
                no_charge_hours.add(h)
            elif dtype == "no_discharge_window":
                no_discharge_hours.add(h)
            elif dtype == "max_grid_window":
                cap = adj["max_grid_kwh"]
                max_grid[h] = cap if max_grid[h] is None else min(max_grid[h], cap)

    prob = pulp.LpProblem("gridwise_schedule", pulp.LpMinimize)

    grid = [pulp.LpVariable(f"grid_{h}", lowBound=0) for h in range(n)]
    solar_used = [pulp.LpVariable(f"solar_used_{h}", lowBound=0) for h in range(n)]
    charge = [pulp.LpVariable(f"charge_{h}", lowBound=0) for h in range(n)]
    discharge = [pulp.LpVariable(f"discharge_{h}", lowBound=0) for h in range(n)]
    energy = [pulp.LpVariable(f"energy_{h}", lowBound=0) for h in range(n)]

    prob += pulp.lpSum(grid[h] * tariff[h] for h in range(n))

    for h in range(n):
        prob += solar_used[h] <= effective_solar[h]
        prob += charge[h] <= battery["max_charge_kwh_per_hour"]
        prob += discharge[h] <= battery["max_discharge_kwh_per_hour"]
        prob += energy[h] >= min_reserve[h]
        prob += energy[h] <= battery["capacity_kwh"]

        # energy balance
        prob += grid[h] + solar_used[h] + discharge[h] == demand[h] + charge[h]

        # battery state transition
        prev_energy = battery["initial_energy_kwh"] if h == 0 else energy[h - 1]
        prob += energy[h] == prev_energy + charge[h] - discharge[h]

        if h in no_charge_hours:
            prob += charge[h] == 0
        if h in no_discharge_hours:
            prob += discharge[h] == 0
        if max_grid[h] is not None:
            prob += grid[h] <= max_grid[h]

    # end-of-day battery neutrality
    prob += energy[n - 1] == battery["initial_energy_kwh"]

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise InfeasibleScenarioError(
            f"Solver returned status: {pulp.LpStatus[status]}"
        )

    plan = []
    total_grid = 0.0
    total_cost = 0.0
    peak_grid = 0.0

    for h in range(n):
        g = max(0.0, pulp.value(grid[h]))
        s = max(0.0, pulp.value(solar_used[h]))
        c = max(0.0, pulp.value(charge[h]))
        dch = max(0.0, pulp.value(discharge[h]))
        e_after = max(0.0, pulp.value(energy[h]))

        net = c - dch
        if net > TOL:
            action, magnitude = "charge", net
        elif net < -TOL:
            action, magnitude = "discharge", -net
        else:
            action, magnitude = "idle", 0.0

        plan.append(
            {
                "hour": h,
                "grid_kwh": round(g, 6),
                "solar_used_kwh": round(s, 6),
                "battery_action": action,
                "battery_kwh": round(magnitude, 6),
                "battery_energy_after_kwh": round(e_after, 6),
            }
        )
        total_grid += g
        total_cost += g * tariff[h]
        peak_grid = max(peak_grid, g)

    return {
        "hourly_plan": plan,
        "total_grid_kwh": round(total_grid, 6),
        "total_cost_bdt": round(total_cost, 6),
        "peak_grid_kwh": round(peak_grid, 6),
    }
