"""The approval-path engine. Plain Python rules, no AI.

Input: site facts (zone, lot size, utilities) + what the person wants to build.
Output: checks, the likely approval path, step-by-step process with who/when/citation,
next filing dates, and who to call. The AI only explains this result in plain English (explain()).
"""
import datetime as dt
from pathlib import Path

import yaml

import llm

RULES = yaml.safe_load((Path(__file__).parent / "data/cumberland/rules.yaml").read_text())

PROJECTS = {
    "adu": "Add an accessory dwelling unit (ADU)",
    "two_family": "Build or convert to a two-family home",
    "multifamily": "Build a building with 3 or more units",
    "reuse": "Convert a commercial building to housing",
    "mixed_use": "Mixed-use: homes plus shops or offices",
}


def _step(title, who, what, time="", cite=""):
    return {"title": title, "who": who, "what": what, "time": time, "cite": cite}


def next_dates(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    for deadline, trc, pb in RULES["schedule_2026"]["rows"]:
        if dt.date.fromisoformat(deadline) >= today:
            return {"deadline": deadline, "trc": trc, "planning_board": pb, "note": RULES["schedule_2026"]["note"]}
    upcoming = [pb for _, _, pb in RULES["schedule_2026"]["rows"] if dt.date.fromisoformat(pb) >= today]
    return {"deadline": None, "trc": None, "planning_board": None, "upcoming_pb": upcoming,
            "note": "The 2026 schedule has no filing deadlines left, and the 2027 schedule isn't posted yet. "
                    "Ask the Planning Clerk (ext. 140) for the next filing deadline."}


def required_lot(dim: dict, units: int) -> int | None:
    """§ 5-2: first unit's area plus an increment for each additional unit. None = more units not allowed here."""
    if units <= 1:
        return dim["first_unit"]
    if not dim.get("per_extra_unit"):
        return None
    if dim.get("max_units") and units > dim["max_units"]:
        return None
    return dim["first_unit"] + dim["per_extra_unit"] * (units - 1)


def evaluate(site: dict, project: str, lot_sf: float | None, utilities: str, units: int = 2,
             adu_in_footprint: bool = False, adu_disability: bool = False, house_sf: float | None = None,
             affordable: bool = False, new_street: bool = False, building_sf: float | None = None) -> dict:
    zone = site.get("zone")
    checks, steps, relief, confirm = [], [], [], []
    verdict = "needs_review"
    path, decided_by = "", ""
    dim_cite = RULES["dimensional"]["cite"]

    if zone not in RULES["zones"]:
        return {"project_key": project, "verdict": "unknown", "headline": f"Zone '{zone}' isn't in this tool's Cumberland rules. Call Planning.",
                "checks": [], "steps": [], "relief": [], "confirm": [], "path": "", "decided_by": "",
                "dates": next_dates(), "contacts": RULES["contacts"], "site": site, "project": project}

    # ---------- 1. Is the use allowed in this zone? (Table 1) ----------
    use_key = {"adu": "adu", "two_family": "two_family", "multifamily": "multifamily", "reuse": None,
               "mixed_use": "mixed_use"}[project]
    if use_key:
        u = RULES["uses"][use_key]
        allowed = u[zone]
        checks.append({"label": f"Use allowed in {zone}?", "result": {"Y": "ok", "S": "warn", "N": "problem"}[allowed],
                       "detail": f"{u['label']}: " + {"Y": "allowed", "S": "needs a special use permit", "N": "not allowed"}[allowed],
                       "cite": "Appendix B, Table 1 (Use Table)"})
        if allowed == "N":
            verdict = "not_allowed"
            relief.append("Use variance: a high bar (the hardship must leave no beneficial use of the property). "
                          "A rezoning by the Town Council is the other route.")
        elif allowed == "S":
            relief.append("Special use permit")
    else:  # adaptive reuse: state law makes it a permitted use if >= 50% of floor area becomes residential
        checks.append({"label": "Adaptive reuse allowed?", "result": "ok",
                       "detail": "State law makes converting an office, school, religious, medical or mall building a "
                                 "permitted use when at least 50% of its floor area becomes housing.",
                       "cite": "R.I. Gen. Laws § 45-24-37 (as amended 2025)"})
        confirm.append("Confirm the building type qualifies and the current density, setback and height rules in "
                       "§ 45-24-37 (amended 2025). Projects that stay in the footprint with public water and sewer "
                       "can't be held below 15 units/acre and must make 20% of units affordable.")

    # ---------- 2. Lot size / density (§ 5-2) ----------
    dims = RULES["dimensional"].get(utilities, {})
    dim = dims.get(zone)
    total_units = {"adu": 1, "two_family": 2, "multifamily": units, "reuse": units, "mixed_use": units}[project]
    if project != "reuse" and zone.startswith(("A-", "R-")):
        if not dim:
            checks.append({"label": "Lot size rule", "result": "unknown",
                           "detail": f"§ 5-2(a) says its table doesn't apply to {zone} without public water or sewer.",
                           "cite": dim_cite})
            confirm.append("Ask Planning which lot-size rule applies without public water and sewer.")
        elif lot_sf:
            need = required_lot(dim, total_units)
            if need is None:
                checks.append({"label": "Units allowed by lot-size rule", "result": "problem",
                               "detail": f"{zone} allows at most {dim.get('max_units', 1)} unit(s) per lot.",
                               "cite": dim_cite})
                verdict = "not_allowed"
            else:
                short = (need - lot_sf) / need if lot_sf < need else 0
                ok = short == 0
                detail = (f"Needs {need:,.0f} sq ft for {total_units} unit(s); lot is {lot_sf:,.0f} sq ft.")
                if ok:
                    checks.append({"label": "Lot size for the units", "result": "ok", "detail": detail, "cite": dim_cite})
                elif project == "adu":
                    # The house is already there: an undersized lot is an existing nonconformity, not new density.
                    checks.append({"label": "Lot size", "result": "warn", "detail":
                                   f"Lot is {lot_sf:,.0f} sq ft, under the {need:,.0f} sq ft minimum. Inside an existing "
                                   "footprint, an ADU needs no relief for an existing nonconformity; outside it, ask Planning.",
                                   "cite": "§ 6-6(h)-(i); " + dim_cite})
                    confirm.append("Confirm with Planning that the existing house and lot are legally nonconforming.")
                else:
                    pct = short * 100
                    detail += f" Short by {pct:.1f}%."
                    if pct <= RULES["thresholds"]["modification_max_pct"]:
                        checks.append({"label": "Lot size", "result": "warn", "detail": detail +
                                       " Small enough for a Zoning Officer modification.", "cite": dim_cite + "; § 18-4"})
                        relief.append(f"Modification ({pct:.1f}% of the lot-area rule; up to 15% allowed). "
                                      + ("No public notice needed at 5% or less." if pct <= 5 else
                                         "Abutters get 14 days to object."))
                    else:
                        checks.append({"label": "Lot size", "result": "problem", "detail": detail +
                                       " Needs a dimensional variance.", "cite": dim_cite + "; § 18-2"})
                        relief.append("Dimensional variance for lot area")
                        if verdict != "not_allowed":
                            verdict = "needs_relief"
                    confirm.append("If this is an existing lot of record, state law on substandard lots "
                                   "(§ 45-24-38, amended 2023 to 2026) may remove the need for relief. Ask Planning.")
            if dim:
                checks.append({"label": f"{zone} setbacks and height", "result": "info",
                               "detail": f"Front {dim['front']} ft, side {dim['side']} ft, rear {dim['rear']} ft, "
                                         f"coverage up to {dim['coverage']:.0%}, height up to {dim['height']} ft, "
                                         f"frontage {dim['frontage']} ft.", "cite": dim_cite})
        else:
            checks.append({"label": "Lot size", "result": "unknown", "detail": "Enter the lot size to check density.",
                           "cite": dim_cite})

    # ---------- 3. Overlays ----------
    if any("Historic" in o for o in site.get("overlays", [])):
        checks.append({"label": "Historic district", "result": "warn",
                       "detail": "Inside a historic district overlay. Exterior changes need a Certificate of "
                                 "Appropriateness from the Historic District Commission.", "cite": "Appendix B Article 8, § 8-6"})
        steps.append(_step("Historic District Commission", "Historic District Commission",
                           "Get a Certificate of Appropriateness for exterior work.", "", "Appendix B § 8-6"))

    # ---------- 4. The path ----------
    t = RULES["timelines"]
    has_relief = bool(relief) and verdict != "not_allowed"
    variance_relief = any("variance" in r.lower() or "special use" in r.lower() for r in relief)

    if project == "adu":
        a = RULES["adu"]
        by_right = adu_disability or adu_in_footprint or (lot_sf or 0) >= 20000
        reason = ("disability accommodation" if adu_disability else "inside the existing footprint" if adu_in_footprint
                  else "lot is 20,000 sq ft or more" if (lot_sf or 0) >= 20000 else None)
        checks.append({"label": "ADU by right?", "result": "ok" if by_right else "problem",
                       "detail": f"Yes: {reason}." if by_right else
                       "None of the 3 by-right conditions is met (lot under 20,000 sq ft, outside the existing footprint, "
                       "no disability accommodation).", "cite": a["cite"]})
        if house_sf:
            cap1, cap2 = min(0.6 * house_sf, 900), min(0.6 * house_sf, 1200)
            checks.append({"label": "Max ADU size", "result": "info",
                           "detail": f"Studio/1 BR: up to {cap1:,.0f} sq ft · 2 BR: up to {cap2:,.0f} sq ft "
                                     f"(60% of {house_sf:,.0f} sq ft house, capped at 900/1,200).", "cite": "§ 6-6(g)"})
        if by_right and verdict != "not_allowed":
            verdict = "by_right"
            path, decided_by = "Building permit only (ADU by right)", "Building Official"
            steps += [
                _step("Talk to Planning (optional)", "Planning Dept.",
                      "Confirm the zone and that the ADU meets § 6-6. You can ask for a written zoning certificate.",
                      "Zoning certificate due within 20 days of a written request (§ 45-24-54)", "§ 6-6; § 45-24-54"),
                _step("Apply for a building permit", "Building Official",
                      "Submit plans showing the ADU floor area, bedrooms (max 2), and 1 parking space per bedroom.",
                      "", "§ 6-6(d), (e), (g); Appendix B § 17-2"),
                _step("Inspections and certificate of occupancy", "Building Official",
                      "Build, pass inspections, get the certificate of occupancy. No short-term rental.", "", "§ 17-3; § 6-6(j)"),
            ]
        else:
            verdict = "needs_relief" if verdict != "not_allowed" else verdict
            path, decided_by = "Not by right: talk to Planning about relief", "Zoning Board of Review (or Planning Board via UDR)"
            relief.append("Relief for an ADU that doesn't meet the § 6-6(a) conditions")
            steps.append(_step("Pre-application conversation", "Planning Director",
                               "Ask whether your ADU can qualify (e.g. fit it inside the existing footprint) or what relief is needed.",
                               "", "§ 6-6(a)"))

    elif project == "two_family" and verdict != "not_allowed":
        if has_relief:
            path, decided_by = "Relief first, then building permit", "Zoning Officer / Zoning Board of Review"
        else:
            verdict, path, decided_by = "by_right", "Building permit (two-family allowed in this zone)", "Building Official"
        confirm.append("Converting an existing single-family home is a change of use. Ask Planning whether "
                       "Administrative Development Plan Review applies (§ 5-I(b)(1)).")
        steps.append(_step("Confirm with Planning", "Planning Dept.",
                           "Confirm the zone, lot area rule (§ 5-2), and whether Development Plan Review applies.",
                           "", "§ 5-2; Appendix A § 5-I"))

    elif project in ("multifamily", "reuse", "mixed_use") and verdict != "not_allowed":
        n = units
        if affordable:
            confirm.append("With income-restricted units you may use a single Comprehensive Permit at the Planning Board "
                           "instead (Appendix B Article 15; R.I. Gen. Laws § 45-53-4).")
        if project == "mixed_use":
            path = "Minor Land Development (mixed-use) or Formal Development Plan Review"
            decided_by = "Administrative Officer or Planning Board"
            confirm.append("Mixed-use with up to 6 dwelling units and 2,500 sq ft or less of commercial space is a Minor Land "
                           "Development (Appendix A § 2) and also listed under Formal Development Plan Review (§ 5-I(b)(2)). "
                           "Ask the Planning Director which applies. Commercial-zone setbacks and height (Appendix B § 5-3) "
                           "are not checked by this tool.")
        elif project == "reuse":
            path = "Development Plan Review or Minor Land Development (the Administrative Officer classifies)"
            decided_by = "Planning Board (Formal Development Plan Review) or Administrative Officer"
            confirm.append("Adaptive reuse with no extensive exterior work is Formal Development Plan Review (§ 5-I(b)(2)). "
                           "In a commercial zone, up to 25,000 sq ft can be a Minor Land Development (Appendix A § 2). "
                           "Ask the Planning Director which applies.")
        elif n <= RULES["thresholds"]["minor_ld_max_units"]:
            board = has_relief and variance_relief or new_street
            path = "Minor Land Development" + (" with Unified Development Review (public hearing)" if board else "")
            decided_by = "Planning Board" if board else "Administrative Officer"
        else:
            path, decided_by = "Major Land Development (Master → Preliminary → Final)", "Planning Board"
        if verdict != "needs_relief":
            verdict = "needs_review"

        major = "Major" in path
        steps.append(_step("Pre-application meeting", "Planning Director + TRC",
                           "Required for major projects; you can ask for one on any project. Bring a concept plan.",
                           "Within 60 days of the pre-application filing", "Appendix A § 5-E"))
        if any("Modification" in r for r in relief):
            steps.append(_step("Request a modification", "Zoning Officer",
                               "Land development projects that need a modification apply to the Zoning Officer first.",
                               t["modification"], "Appendix B § 18-4; 2026 schedule"))
        if major:
            steps += [
                _step("Master Plan", "Planning Board", "Submit the master plan checklist. Public hearing at this stage.",
                      t["major_certify"], "Appendix A § 5-H"),
                _step("Preliminary Plan", "Planning Board", "Engineering-level plans, state permits as needed.", "", "Appendix A § 5-H"),
                _step("Final Plan + recording", "Administrative Officer", "Final checklist, guarantees, recording.", "", "Appendix A § 5-H"),
            ]
        else:
            steps += [
                _step("File the preliminary plan" + (" + UDR request" if "Unified" in path else ""), "Administrative Officer",
                      "Submit the preliminary plan with the completed checklist (Appendix D)."
                      + (" Variances/special use permits are decided together with the plan under Unified Development Review."
                         if "Unified" in path else ""),
                      t["minor_certify_days"], "Appendix A § 5-G(b)-(d)"),
                _step("Technical Review Committee", "TRC (Planning, DPW, Engineer, Building, Fire, Police)",
                      "Every project is reviewed by the TRC first. It meets monthly at 3:00 pm.", "", "2026 schedule"),
                _step("Decision on the preliminary plan", decided_by,
                      "Public hearing first if there's UDR or a new street." if "Unified" in path else "Administrative decision.",
                      t["minor_decision_board"] if "Unified" in path else t["minor_decision_admin"], "Appendix A § 5-G(g)"),
                _step("Final plan + recording", "Administrative Officer",
                      "Final checklist, construction schedule/guarantees, tax certificate.", t["minor_expiry"], "Appendix A § 5-G(j)-(k)"),
            ]
        steps.append(_step("Building permit", "Building Official", "After approval is recorded.", "", "Appendix B § 17-2"))

    if verdict == "not_allowed":
        path = path or "Not allowed as proposed"
        decided_by = decided_by or "Zoning Board of Review (use variance) or Town Council (rezoning)"
        steps.insert(0, _step("Talk to the Planning Director before spending money", "Planning Director",
                              "Ask about alternatives: a different housing type allowed in this zone, fewer units, or rezoning.",
                              "", "Appendix B Table 1"))

    if verdict not in ("not_allowed", "by_right") and relief:
        only_mod = all(r.startswith("Modification") for r in relief)
        verdict = "needs_modification" if only_mod else "needs_relief"
        if only_mod and project == "two_family":
            path = "Zoning Officer modification, then building permit"
            decided_by = "Zoning Officer, then Building Official"
            steps.insert(0, _step("Request a modification", "Zoning Officer",
                                  "Ask for the lot-area modification before applying for the building permit.",
                                  t["modification"], "Appendix B § 18-4"))
        elif project == "two_family":
            steps.insert(0, _step("Apply for a dimensional variance", "Zoning Board of Review",
                                  "File with the Zoning Clerk by the first Friday of the month. Public hearing.",
                                  "", "Appendix B §§ 18-1, 18-2; 2026 schedule"))

    headline = {
        "by_right": "Likely allowed by right. No board hearing expected.",
        "needs_review": "Allowed in this zone, but it needs town plan review.",
        "needs_modification": "Likely possible with a small modification from the Zoning Officer.",
        "needs_relief": "Possible, but it needs zoning relief (a variance or special use permit) first.",
        "not_allowed": "Not allowed as proposed in this zone.",
    }.get(verdict, "Check with Planning")

    confirm += [f"Confirm the lot really has {'public water and sewer' if utilities == 'both' else 'the utilities you selected'}: "
                "the lot-size rule (§ 5-2) changes with it. Ask Cumberland DPW Sewer (ext. 122) and your water utility "
                "(Pawtucket Water Supply Board in Valley Falls, Cumberland Water north of Marshall Ave).",
                "Confirm the zone on the town's official zoning map. This tool uses the statewide RIGIS layer.",
                "Check for wetlands, flood zones and easements. This tool doesn't check them.",
                "Check for amendments not yet in the code (eCode360 lists 'New Laws', e.g. Ord. 26-17A and 26-22)."]

    return {"project_key": project, "verdict": verdict, "headline": headline, "path": path, "decided_by": decided_by,
            "checks": checks, "relief": relief, "steps": steps, "confirm": confirm,
            "dates": next_dates(), "contacts": RULES["contacts"], "site": site, "project": PROJECTS[project],
            "inputs": {"lot_sf": lot_sf, "utilities": utilities, "units": total_units}}



# ---------------------------------------------------------------------------
# Everything this lot can hold, side by side
# ---------------------------------------------------------------------------
OPTION_META = {
    "adu": ("🏡", "Add an ADU"),
    "two_family": ("🏘️", "Two-family home"),
    "multifamily": ("🏢", "3+ unit building"),
    "reuse": ("🏬", "Commercial to housing"),
    "mixed_use": ("🏙️", "Mixed-use (homes + shops)"),
}


def max_units(dim: dict | None, lot_sf: float | None) -> int | None:
    """§ 5-2: how many dwelling units the lot AREA supports (before any relief)."""
    if not dim or not lot_sf:
        return None
    if lot_sf < dim["first_unit"]:
        return 0
    if not dim.get("per_extra_unit"):
        return 1
    n = 1 + int((lot_sf - dim["first_unit"]) // dim["per_extra_unit"])
    return min(n, dim.get("max_units", n))


def site_rules(site: dict, lot_sf: float | None, utilities: str) -> dict | None:
    dim = RULES["dimensional"].get(utilities, {}).get(site.get("zone"))
    if not dim:
        return None
    cov = dim["coverage"]
    return {"zone": site["zone"], "min_lot": dim["min_lot"], "frontage": dim["frontage"], "front": dim["front"],
            "side": dim["side"], "rear": dim["rear"], "coverage": cov, "height": dim["height"],
            "max_footprint": round(lot_sf * cov) if lot_sf else None,
            "max_units_by_area": max_units(dim, lot_sf), "cite": RULES["dimensional"]["cite"]}


def options(site: dict, lot_sf: float | None, utilities: str, house_sf: float | None = None) -> list[dict]:
    """Evaluate every housing type for this lot. Each option has a verdict, headline, and one 'what to know' line."""
    zone = site.get("zone")
    dim = RULES["dimensional"].get(utilities, {}).get(zone)
    mu = max_units(dim, lot_sf)
    out = []
    for key in ("adu", "two_family", "multifamily", "reuse", "mixed_use"):
        icon, title = OPTION_META[key]
        kw, detail = {}, ""
        if key == "adu":
            base = evaluate(site, key, lot_sf, utilities, house_sf=house_sf)
            inside = evaluate(site, key, lot_sf, utilities, house_sf=house_sf, adu_in_footprint=True)
            res = base
            if base["verdict"] == "not_allowed":
                detail = "ADUs are only on the use table for A and R zones, not " + str(zone) + "."
            elif lot_sf and lot_sf >= 20000:
                detail = "Lot is 20,000 sq ft or more, so one ADU is allowed by right (state law and § 6-6)."
            elif inside["verdict"] == "by_right" and base["verdict"] != "by_right":
                res = {**base, "verdict": "conditional",
                       "headline": "Allowed by right if it fits inside the existing house or garage footprint."}
                detail = "Outside the existing footprint on a lot under 20,000 sq ft, it needs relief."
            elif base["verdict"] == "by_right":
                detail = "A by-right condition is met."
            else:
                detail = "Only in R/A zones. Ask Planning if another route applies."
        elif key == "two_family":
            res = evaluate(site, key, lot_sf, utilities)
            detail = "Allowed in R-2 and R-3. Needs 10,000 sq ft + 5,000 for the 2nd unit with water and sewer." \
                if zone in ("R-2", "R-3") else f"Not on the use table for {zone}."
        elif key == "multifamily":
            n = max(3, mu or 3)
            res = evaluate(site, key, lot_sf, utilities, units=n)
            detail = (f"Lot area supports up to {mu} unit(s) by right; showing {n} units." if mu is not None and mu >= 3
                      else f"Lot area supports {mu if mu is not None else '?'} unit(s) by right; showing the 3-unit minimum.") \
                if zone == "R-3" else f"Only allowed in R-3 (this lot is {zone})."
        elif key == "mixed_use":
            res = evaluate(site, key, lot_sf, utilities, units=6)
            detail = ("Allowed in C-1 and C-2. A project of up to 6 homes and 2,500 sq ft of shops is a Minor Land Development. "
                      "Commercial-zone dimensions (§ 5-3) aren't checked here.") if zone in ("C-1", "C-2") \
                else f"Mixed-use residential is only on the use table for C-1 and C-2, not {zone}."
        else:
            res = evaluate(site, key, lot_sf, utilities, units=8)
            detail = "Needs an existing commercial building (office, school, church, medical, mall) where at least 50% becomes housing."
        label_cls, label = {"by_right": ("ok", "Allowed by right"), "conditional": ("info", "By right if inside footprint"),
                            "needs_review": ("info", "Allowed, needs plan review"),
                            "needs_modification": ("info", "Small modification"),
                            "needs_relief": ("warn", "Needs relief"), "not_allowed": ("bad", "Not allowed here"),
                            "unknown": ("warn", "Ask the town")}[res["verdict"]]
        out.append({"project": key, "icon": icon, "title": title, "verdict": res["verdict"], "cls": label_cls,
                    "label": label, "headline": res["headline"], "path": res["path"], "decided_by": res["decided_by"],
                    "detail": detail, "units": res.get("inputs", {}).get("units")})
    rank = {"by_right": 0, "conditional": 1, "needs_modification": 2, "needs_review": 3, "needs_relief": 4, "unknown": 5, "not_allowed": 6}
    resi = str(zone).startswith(("A-", "R-"))  # a conversion needs a commercial building, which is unusual in A/R zones
    return sorted(out, key=lambda o: rank.get(o["verdict"], 9) + (0.5 if o["project"] == "reuse" and resi else 0))


# ---------------------------------------------------------------------------
# Checklist of documents and fee estimate for a chosen path
# ---------------------------------------------------------------------------
def documents_for(res: dict) -> list[dict]:
    """Which document checklists apply to this path. Each returns {key, title, items:[{t,c}]}."""
    docs, pk, path = RULES["documents"], res["project_key"], res["path"]
    rel = " ".join(res.get("relief", [])).lower()
    keys = []
    if pk in ("multifamily", "reuse", "mixed_use"):
        keys += ["general_plan_requirements", "pre_application"]
    if "modification" in rel:
        keys.append("zoning_modification")
    if "variance" in rel or ("special use" in rel) or (pk == "adu" and res["verdict"] in ("needs_relief",)):
        keys.append("variance")
    if pk in ("multifamily", "reuse", "mixed_use"):
        if "Major" in path:
            keys += ["master_plan", "preliminary_plan", "final_plan"]
        else:
            keys += ["preliminary_plan", "final_plan"]
    if pk == "adu":
        keys.append("building_permit_adu")
    else:
        keys.append("building_permit_general")
    seen, out = set(), []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append({"key": k, "title": docs[k]["title"], "items": docs[k]["items"]})
    return out


def fee_estimate(res: dict) -> dict:
    """Planning fees from Appendix A. Zoning-relief and building-permit fees aren't in the posted schedule."""
    f, pk, path = RULES["fees"], res["project_key"], res["path"]
    units = res.get("inputs", {}).get("units") or 1
    lines = []
    if pk in ("multifamily", "reuse", "mixed_use"):
        def amt(stage):
            base, per = f["per_unit_stage"][stage]
            return base + per * units, f"${base} + ${per} × {units} unit(s)"
        stages = [("Pre-application", "pre_application")]
        stages += [("Master plan", "master_plan")] if "Major" in path else []
        stages += [("Preliminary plan", "preliminary_plan"), ("Final plan", "final_plan")]
        for name, k in stages:
            a, formula = amt(k)
            lines.append({"item": name, "amount": a, "formula": formula})
    other = []
    if "modification" in " ".join(res.get("relief", [])).lower() or "variance" in " ".join(res.get("relief", [])).lower():
        other.append("Zoning modification / variance / special use permit filing fee: not in the posted schedule. Ask the Zoning Clerk.")
    other.append("Building permit fee: not in the posted schedule. Ask the Building Official.")
    other.append("Also possible: consultant peer review (§ 5-M), newspaper and mailing notice costs, recording fees, NBC sewer permit.")
    return {"lines": lines, "total": sum(x["amount"] for x in lines), "other": other, "cite": f["cite"], "note": f["note"]}


EXPLAIN_SYSTEM = """You explain a town approval process to a small developer or homeowner in plain, simple English.
Use ONLY the facts in the JSON you are given. Do not add rules, numbers, or sections that are not in it.
Keep section citations exactly as given (e.g. "§ 5-G(g)"). Say clearly this is not legal advice.
Return JSON: {"summary": "4-6 short sentences", "questions_for_planner": ["3 specific questions to ask the town planner"]}"""


def explain(result: dict) -> dict:
    slim = {k: result[k] for k in ("headline", "path", "decided_by", "checks", "relief", "steps", "confirm", "project", "inputs")}
    slim["zone"] = result["site"].get("zone")
    try:
        import json
        return llm.complete_json(EXPLAIN_SYSTEM, json.dumps(slim), max_tokens=700)
    except Exception as e:
        return {"summary": f"{result['headline']} Path: {result['path']}. Decided by: {result['decided_by']}.",
                "questions_for_planner": [], "error": type(e).__name__}


if __name__ == "__main__":
    import json
    import sys

    import lookup

    addr = " ".join(sys.argv[1:]) or "25 School St"
    m = lookup.find_address(addr)[0]
    site = lookup.site_facts(m["lon"], m["lat"])
    for proj, kw in [("adu", {"lot_sf": 12000, "house_sf": 1400}), ("two_family", {"lot_sf": 14000}),
                     ("multifamily", {"lot_sf": 30000, "units": 5}), ("multifamily", {"lot_sf": 26000, "units": 5})]:
        r = evaluate(site, proj, utilities="both", **kw)
        print(f"\n== {m['address']} [{site['zone']}] {proj} {kw}\n{r['headline']} | {r['path']} | {r['decided_by']}")
        for c in r["checks"]:
            print("  -", c["result"], c["label"], ":", c["detail"])
        print("  relief:", r["relief"])
