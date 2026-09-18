"""
Quick local sanity check against the Public Sample Cases pack.
Not the hidden judge — just catches broken schema / energy-balance bugs
before you submit.

Usage:
    python test_public_samples.py path/to/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
"""
import json
import sys

import requests

BASE_URL = "http://localhost:8000"
TOL = 0.05  # kWh/BDT slack for local sanity checks (judge uses 0.01)


def check_case(case: dict) -> list[str]:
    problems = []
    resp = requests.post(f"{BASE_URL}/optimize-energy", json=case["input"], timeout=30)
    if resp.status_code != 200:
        return [f"HTTP {resp.status_code}: {resp.text[:300]}"]

    body = resp.json()
    notes = case["input"]["operator_notes"]
    di = body.get("directive_interpretation", [])

    if len(di) != len(notes):
        problems.append(f"directive_interpretation has {len(di)} entries, expected {len(notes)}")
    for i, entry in enumerate(di):
        if entry.get("note_index") != i:
            problems.append(f"entry {i} has note_index {entry.get('note_index')}, expected {i}")
        if entry["directive_type"] == "no_op" and entry["applies"] is not False:
            problems.append(f"entry {i}: no_op must have applies=False")
        if entry["directive_type"] != "no_op" and entry["applies"] is not True:
            problems.append(f"entry {i}: non-no_op directive must have applies=True")

    plan = body.get("hourly_plan", [])
    if len(plan) != 24 or sorted(p["hour"] for p in plan) != list(range(24)):
        problems.append("hourly_plan does not contain exactly hours 0..23")

    battery = case["input"]["battery"]
    hours_in = {h["hour"]: h for h in case["input"]["hours"]}
    prev_energy = battery["initial_energy_kwh"]
    for p in sorted(plan, key=lambda x: x["hour"]):
        h = p["hour"]
        demand = hours_in[h]["demand_kwh"]
        lhs = p["grid_kwh"] + p["solar_used_kwh"]
        lhs += p["battery_kwh"] if p["battery_action"] == "discharge" else 0
        rhs = demand + (p["battery_kwh"] if p["battery_action"] == "charge" else 0)
        if abs(lhs - rhs) > TOL:
            problems.append(f"hour {h}: energy balance off by {lhs - rhs:.3f} kWh")

        expected_delta = (
            p["battery_kwh"] if p["battery_action"] == "charge"
            else -p["battery_kwh"] if p["battery_action"] == "discharge"
            else 0
        )
        if abs((prev_energy + expected_delta) - p["battery_energy_after_kwh"]) > TOL:
            problems.append(f"hour {h}: battery_energy_after_kwh inconsistent with battery_action/kwh")
        prev_energy = p["battery_energy_after_kwh"]

    if abs(prev_energy - battery["initial_energy_kwh"]) > TOL:
        problems.append(
            f"end-of-day battery energy {prev_energy:.2f} != initial {battery['initial_energy_kwh']}"
        )

    recompute_grid = sum(p["grid_kwh"] for p in plan)
    if abs(recompute_grid - body.get("total_grid_kwh", -1)) > TOL:
        problems.append("total_grid_kwh does not match sum of hourly_plan")

    return problems


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    with open(path) as f:
        data = json.load(f)

    total = 0
    failed = 0
    for case in data["cases"]:
        total += 1
        problems = check_case(case)
        if problems:
            failed += 1
            print(f"[FAIL] {case['id']} ({case.get('label', '')})")
            for p in problems:
                print(f"    - {p}")
        else:
            print(f"[OK]   {case['id']} ({case.get('label', '')})")

    print(f"\n{total - failed}/{total} cases passed local sanity checks.")


if __name__ == "__main__":
    main()
