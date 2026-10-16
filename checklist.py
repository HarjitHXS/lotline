"""Build the approval checklist for one project: who to call, what to bring, what to file, what to confirm.

Each item has a stable key (so ticks, notes and uploads stay attached to it), a phase, and a kind. The kinds
borrow the US utility-marking colors used on pavement before anyone digs:
  orange = town contact (communications), green = sewer, blue = water, pink = survey and plans, ink = forms and filing,
  yellow = double-check.
"""
import re

import process

R = process.RULES
PHASES = [("check", "Check the basics"), ("talk", "Talk to the town and utilities"), ("docs", "Get documents ready"),
          ("file", "File and get reviewed"), ("build", "Before you build")]
KINDS = {"town": "Town contact", "sewer": "Sewer", "water": "Water", "plans": "Plans and survey", "form": "Forms and filing",
         "check": "Double-check"}


def _contact(name: str) -> dict | None:
    return next((c for c in R["contacts"] if c["name"] == name), None)


def _water_contact(site: dict) -> dict:
    sup = site.get("water_suppliers") or []
    uc = R["utility_contacts"]
    if sup == ["Cumberland"]:
        return uc["cumberland_water"]
    if sup == ["Pawtucket"]:
        return uc["pwsb"]
    c = dict(uc["pwsb"])
    c["title"] = ("The map shows " + " and ".join(sup) if sup else "No supplier found on the map") + ". Ask which one serves your lot"
    return c


def doc_kind(text: str) -> str:
    low = text.lower()
    if "sewer" in low or "public works" in low:
        return "sewer"
    if "water" in low and re.search(r"certif|supplier|service", low):
        return "water"
    if re.search(r"survey|\bplans?\b|prints|drawing|elevation|floor|\bmap\b|contours|sheets|plat\b|profile|landscape|lighting", low):
        return "plans"
    return "form"


def _when(cond: str, pj: dict, res: dict) -> bool:
    pk, util, site = pj["choice"], pj["utilities"], pj["site"]
    return {
        "always": True,
        "ld": pk in ("multifamily", "reuse", "mixed_use"),
        "relief": bool(res["relief"]) and res["verdict"] not in ("by_right",),
        "sewer": util in ("both", "one") or bool(site.get("sewer_area")),
        "historic": any("Historic" in o for o in site.get("overlays", [])),
        "water_any": util in ("both", "one"),
        "adu_or_two_or_ld": util in ("both", "one"),
    }[cond]


def build(pj: dict, res: dict) -> list[dict]:
    pre, items = pj["choice"], []

    def add(phase, kind, key, title, **kw):
        items.append({"key": f"{pre}|{key}", "phase": phase, "kind": kind, "title": title, "detail": kw.get("detail", ""),
                      "cite": kw.get("cite", ""), "time": kw.get("time", ""), "who": kw.get("who", ""),
                      "contact": kw.get("contact"), "asks": kw.get("asks", []), "expect": kw.get("expect", "")})

    # 1. basics to confirm (the utility confirmation is handled by the sewer and water contact tasks)
    for i, c in enumerate(res["confirm"]):
        if c.startswith("Confirm the lot really has"):
            continue
        add("check", "check", f"check|{i}", c)

    # 2. calls and emails, and the utility tasks
    for t in R["contact_tasks"]:
        if not _when(t["when"], pj, res):
            continue
        if t.get("utility") == "water":
            contact = _water_contact(pj["site"])
        elif t.get("utility"):
            contact = R["utility_contacts"][t["utility"]]
        else:
            contact = _contact(t["contact"])
        add(t["phase"], t["kind"], f"task|{t['id']}", t["title"], detail=t["detail"], cite=t["cite"], contact=contact,
            asks=t["asks"], expect=t["expect"])

    # 3. documents to prepare
    for d in process.documents_for(res):
        for j, it in enumerate(d["items"]):
            add("docs", doc_kind(it["t"]), f"doc|{d['key']}|{j}", it["t"], cite=it["c"], who=d["title"],
                expect="Your copy of this document")

    # 4. filing and review steps (the last step, the building permit, belongs to the final phase)
    steps = res["steps"]
    for i, s in enumerate(steps):
        phase = "build" if s["title"] == "Building permit" else "file"
        add(phase, "form", f"step|{i}", s["title"], detail=s["what"], cite=s["cite"], time=s["time"], who=s["who"],
            expect="Receipt, approval letter or decision")

    order = {p: i for i, (p, _) in enumerate(PHASES)}
    return sorted(items, key=lambda x: order[x["phase"]])  # stable: keeps the order inside each phase


def progress(pj: dict, items: list[dict]) -> tuple[int, int]:
    return sum(bool(pj["done"].get(i["key"])) for i in items), len(items)
