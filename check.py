"""Step 2: check an application package against a town checklist.

For each checklist item:
  1. Python decides if it applies (e.g. items 25-28 only when zoning relief is requested).
  2. Retrieval: find the few pages most likely to show it (keyword scoring = simple RAG).
  3. The model answers in JSON with an exact quote + file + page.
  4. Python verifies the quote is really on that page. If not, the row becomes "unclear".
Nothing here decides anything: every row is a suggestion for a planner to accept or reject.
"""
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

import llm
from extract import load_package

ROOT = Path(__file__).parent
CHECKLIST = ROOT / "data/providence/checklist.yaml"

RELIEF_PATTERNS = {
    "dimensional variance": r"\bvariance",
    "special use permit": r"special use permit|\bSUP\b",
    "unified development review": r"unified development review|\bUDR\b",
    "dimensional adjustment / parking reduction": r"dimensional adjustment|parking (reduction|adjustment)",
    "administrative modification": r"administrative modification",
}


def load_checklist(path: Path = CHECKLIST) -> dict:
    return yaml.safe_load(path.read_text())


# ---------- deterministic helpers (no AI) ----------

def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def detect_relief(pages: list[dict]) -> dict[str, list[dict]]:
    """Which kinds of zoning relief does the package mention? -> {kind: [where]}"""
    found = defaultdict(list)
    for p in pages:
        for kind, pat in RELIEF_PATTERNS.items():
            m = re.search(pat, p["text"], flags=re.I)
            if m:
                s = max(0, m.start() - 80)
                found[kind].append({"file": p["file"], "page": p["page"],
                                    "snippet": p["text"][s:m.end() + 120]})
    return dict(found)


def best_window(text: str, kws: list[str], size: int = 2500) -> str:
    """Plan sheets are long. Send the model the 2,500-character stretch with the most keyword hits."""
    low = text.lower()
    hits = [(m.start(), 1 + len(kw.split())) for kw in kws for m in re.finditer(re.escape(kw), low)]
    if not hits:
        return text[:size]
    best = max(hits, key=lambda h: sum(w for pos, w in hits if h[0] - 300 <= pos < h[0] - 300 + size))
    s = max(0, best[0] - 300)
    return text[s:s + size]


def retrieve(item: dict, pages: list[dict], k: int = 4) -> list[dict]:
    """Score each page by keyword hits; longer phrases count more. Return top-k with a text window."""
    kws = [kw.lower() for kw in item.get("keywords", [])]
    scored = []
    for p in pages:
        t = p["text"].lower()
        score = sum(t.count(kw) * (1 + len(kw.split())) for kw in kws)
        # Sheet titles like "LIGHTING PLAN" or "LANDSCAPE PLAN" are strong signals.
        score += sum(8 for kw in kws if len(kw.split()) > 1 and f"{kw} plan" in t)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, p in scored[:k]:
        out.append({**p, "window": best_window(p["text"], kws)})
    return out


def verify_quote(quote: str, page: dict | None) -> bool:
    """Is the model's quote really on the page it cited? Allow small differences in spacing/punctuation."""
    if not quote or not page:
        return False
    q, t = norm(quote), norm(page["text"])
    if q and q in t:
        return True
    words = q.split()
    return len(words) >= 3 and sum(w in t for w in words) / len(words) >= 0.85


def find_mismatches(pages: list[dict]) -> list[dict]:
    """Facts that should be identical across documents but aren't. Flag, don't pick."""
    facts: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for p in pages:
        where = f"{p['file']} p.{p['page']}"
        for m in re.finditer(r"\b\d{2}-\d{3}[A-Z]{2,4}\b", p["file"] + " " + p["text"]):
            facts["case number"][m.group(0)].append(where)
        for m in re.finditer(r"(?<!per )(?<![\d,.])(\d{1,3})[ -](?:dwelling[ -])?units?\b", p["text"], flags=re.I):
            if int(m.group(1)) >= 3:
                facts["unit count"][m.group(1)].append(where)
    # Project address: the most-mentioned "<number> <Street> Street"; then flag the same number on another street.
    addr = defaultdict(list)
    for p in pages:
        for m in re.finditer(r"\b(\d{2,5})\s+([A-Z][a-z]+|[A-Z]{3,})\s+(Street|St\.?|Avenue|Ave\.?)(?![a-z])", p["text"]):
            addr[(m.group(1), m.group(2).title())].append(f"{p['file']} p.{p['page']}")
    # Same street number on two different streets (e.g. "309 Dexter" vs "309 Westminster") is suspicious.
    by_num = defaultdict(dict)
    for (n, street), where in addr.items():
        by_num[n][street] = where
    for n, streets in by_num.items():
        if len(streets) > 1 and sum(len(w) for w in streets.values()) >= 3:
            for street, where in streets.items():
                facts[f"address {n}"][f"{n} {street} Street"].extend(where)
    out = []
    for fact, values in facts.items():
        if len(values) > 1:
            out.append({"fact": fact,
                        "values": {v: sorted(set(w))[:6] for v, w in values.items()}})
    return out


# ---------- the AI step ----------

SYSTEM = """You are helping a municipal planner check whether a land development application is complete.
You only report what the provided pages show. You never decide approval and never give legal advice.
Return ONLY a JSON object with these keys:
  "status": "found" | "missing" | "unclear"
  "evidence_quote": an EXACT short quote (5-25 words) copied character-for-character from ONE page below, or "" if missing
  "file": the file name of that page, or ""
  "page": the page number (integer), or null
  "reason": one plain-English sentence a planner can check quickly
Use "found" only if a page clearly shows the required item. Use "unclear" if it is partly shown or you are unsure.
Use "missing" if none of the pages show it. These are excerpts, not the full package, so say so if relevant."""


def ask_model(item: dict, candidates: list[dict]) -> dict:
    if not candidates:
        return {"status": "missing", "evidence_quote": "", "file": "", "page": None,
                "reason": "No page in the package mentions this item's keywords."}
    excerpts = "\n\n".join(
        f"[file: {c['file']} | page: {c['page']}]\n{c['window']}" for c in candidates
    )
    user = (f"Checklist item {item['id']}: {item['text']}\n\n"
            f"Candidate pages from the application:\n\n{excerpts}\n\nReturn the JSON object.")
    try:
        return llm.complete_json(SYSTEM, user)
    except Exception as e:  # never crash the demo on one bad call
        return {"status": "unclear", "evidence_quote": "", "file": "", "page": None,
                "reason": f"Model error, check manually ({type(e).__name__})."}


def check_item(item: dict, pages: list[dict], relief: bool) -> dict:
    base = {"id": item["id"], "requirement": item["text"],
            "cite": f"Appendix C item {item['id']}, p.{item['page']}"}
    if item["applies"] == "udr" and not relief:
        return {**base, "status": "not_applicable", "evidence_quote": "", "file": "", "page": None,
                "reason": "Only required when zoning relief / Unified Development Review is requested.",
                "verified": None}
    if not item.get("in_pdf", True):
        return {**base, "status": "staff_check", "evidence_quote": "", "file": "", "page": None,
                "reason": "Office item (online form, fee, mailing) that a PDF package can't show. Staff confirm.",
                "verified": None}
    cands = retrieve(item, pages)
    ans = ask_model(item, cands)
    status = ans.get("status", "unclear")
    if status not in {"found", "missing", "unclear"}:
        status = "unclear"
    page_obj = next((p for p in pages if p["file"] == ans.get("file") and p["page"] == ans.get("page")), None)
    verified = verify_quote(ans.get("evidence_quote", ""), page_obj) if status != "missing" else None
    reason = ans.get("reason", "")
    if status == "found" and not verified:
        status = "unclear"
        reason = "Model said found, but its quote was not on the cited page. " + reason
    if status == "missing" and item["applies"] == "if_needed":
        reason += " (Only required in some cases; confirm whether it applies.)"
    return {**base, "status": status, "evidence_quote": ans.get("evidence_quote", ""),
            "file": ans.get("file", ""), "page": ans.get("page"), "reason": reason,
            "verified": verified, "scanned": bool(page_obj and page_obj["scanned"]),
            "candidates": [f"{c['file']} p.{c['page']}" for c in cands]}


def run(pages: list[dict], relief: bool | None = None, checklist: dict | None = None, workers: int = 6) -> dict:
    checklist = checklist or load_checklist()
    relief_found = detect_relief(pages)
    if relief is None:
        relief = bool(relief_found)
    n = 1 if llm.provider() == "ollama" else workers  # local model: one at a time
    with ThreadPoolExecutor(n) as ex:
        rows = list(ex.map(lambda it: check_item(it, pages, relief), checklist["items"]))
    return {"checklist": {k: v for k, v in checklist.items() if k != "items"},
            "model": f"{llm.provider()}:{llm.model_name()}",
            "relief_requested": relief, "relief_found": relief_found,
            "mismatches": find_mismatches(pages), "rows": rows}


def applicant_files(folder: Path) -> list[Path]:
    """The staff report is the ANSWER KEY, not part of the application. Keep it out of the input."""
    return [p for p in folder.glob("*.pdf") if not re.search(r"staff-report|DPD-Recommendation|ZBR_", p.name, re.I)]


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    only = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else None
    pages = load_package(applicant_files(folder))
    cl = load_checklist()
    if only:
        cl["items"] = [i for i in cl["items"] if i["id"] in only]
    result = run(pages, checklist=cl)
    out = ROOT / "data/cases" / f"{folder.name.split('_')[0].replace(' ', '_')}_result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    icons = {"found": "✅", "missing": "❌", "unclear": "❓", "not_applicable": "➖", "staff_check": "🗂"}
    print("model:", result["model"], "| relief requested:", result["relief_requested"], list(result["relief_found"]))
    for r in result["rows"]:
        loc = f"{r['file']} p.{r['page']}" if r.get("file") else ""
        print(f"{icons[r['status']]} {r['id']:>2} {r['status']:<14} {loc[:60]:<60} {r['reason'][:90]}")
    for m in result["mismatches"]:
        print("⚠️ mismatch:", m["fact"], list(m["values"]))
    print("saved", out)
