"""Review screen: the planner sees every suggestion and accepts or rejects it.

Run:  uv run streamlit run app.py
"""
import datetime as dt
import json
import tempfile
from pathlib import Path

import streamlit as st

import check
import llm
from extract import load_package

ROOT = Path(__file__).parent
EXAMPLE = ROOT / "data/raw/q2pack/03_Case File Examples/Case A_ 309 Dexter St, Providence"
CACHE = ROOT / "data/cases/Case_A_result.json"
ICON = {"found": "✅ Found", "missing": "❌ Missing", "unclear": "❓ Unclear",
        "not_applicable": "➖ Not applicable", "staff_check": "🗂 Staff check"}

st.set_page_config(page_title="Completeness Pre-Check", page_icon="📋", layout="wide")
cl = check.load_checklist()

st.title("📋 Completeness Pre-Check")
st.caption(f"{cl['town']} · {cl['application_type']} · {cl['stage']} · "
           f"checklist: {cl['source_title']} (version {cl['source_version']}, read {cl['read_date']})")
st.warning(
    "**Draft suggestions for a planner to review, not a decision and not legal advice.** "
    "This version knows only one Providence checklist. It reads only the PDFs you give it, can miss items "
    "on scanned or image-only sheets, and can't see the online form, fees, or mailings. "
    "Each row stays a suggestion until you accept it.", icon="⚠️")

# ---------------- input ----------------
with st.sidebar:
    st.header("1 · Application package")
    source = st.radio("Source", ["Example: Case A, 309 Dexter St", "Upload PDFs"])
    uploads, xcheck = [], []
    if source == "Upload PDFs":
        uploads = st.file_uploader("Applicant's PDFs", type="pdf", accept_multiple_files=True)
        xcheck = st.file_uploader("Optional: your draft staff report, to cross-check facts", type="pdf",
                                  accept_multiple_files=True)
        use_cache = False
    else:
        include_staff = st.checkbox("Cross-check against the city staff report", value=True,
                                    help="Used only to find facts that disagree (case number, address). "
                                         "Never used as evidence for checklist items.")
        use_cache = st.checkbox("Use saved run (fast demo)", value=CACHE.exists(), disabled=not CACHE.exists())

    st.header("2 · Zoning relief")
    relief_choice = st.radio("Does the project request zoning relief (UDR)?",
                             ["Auto-detect", "Yes", "No"], help="Items 25–28 apply only with relief.")
    st.caption(f"Model: `{llm.provider()}:{llm.model_name()}`")
    go = st.button("Run pre-check", type="primary", use_container_width=True)


@st.cache_data(show_spinner=False)
def pages_from_bytes(files: tuple[tuple[str, bytes], ...]) -> list[dict]:
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, data in files:
            p = Path(d) / name
            p.write_bytes(data)
            paths.append(p)
        return load_package(paths)


def get_pages():
    if source == "Upload PDFs":
        app = pages_from_bytes(tuple((f.name, f.getvalue()) for f in uploads or []))
        extra = pages_from_bytes(tuple((f.name, f.getvalue()) for f in xcheck or []))
        return app, extra
    app = load_package(check.applicant_files(EXAMPLE))
    extra = load_package([p for p in EXAMPLE.glob("*.pdf") if p not in check.applicant_files(EXAMPLE)]) \
        if include_staff else []
    return app, extra


if go:
    app_pages, extra_pages = get_pages()
    if not app_pages:
        st.error("Add at least one applicant PDF.")
        st.stop()
    relief = None if relief_choice == "Auto-detect" else relief_choice == "Yes"
    if use_cache:
        result = json.loads(CACHE.read_text())
    else:
        with st.spinner(f"Checking {len(cl['items'])} items across {len(app_pages)} pages…"):
            result = check.run(app_pages, relief=relief)
    # Mismatches look across applicant AND city documents; checklist evidence uses applicant docs only.
    result["mismatches"] = check.find_mismatches(app_pages + extra_pages)
    result["pages_read"] = len(app_pages)
    result["scanned_pages"] = sum(p["scanned"] for p in app_pages)
    st.session_state.result = result
    st.session_state.decisions = {}

result = st.session_state.get("result")
if not result:
    st.info("Pick a package on the left and press **Run pre-check**.")
    st.stop()

rows = result["rows"]
decisions = st.session_state.decisions

# ---------------- summary ----------------
counts = {k: sum(r["status"] == k for r in rows) for k in ICON}
cols = st.columns(5)
for c, (k, label) in zip(cols, ICON.items()):
    c.metric(label, counts[k])
st.caption(f"Read {result.get('pages_read', '?')} applicant pages "
           f"({result.get('scanned_pages', 0)} scanned, little or no text) · model `{result['model']}`")

left, right = st.columns(2)
with left:
    st.subheader("⚠️ Facts that disagree across documents")
    if not result["mismatches"]:
        st.success("None found by the automatic checks.")
    for m in result["mismatches"]:
        with st.container(border=True):
            st.markdown(f"**{m['fact'].capitalize()}**: {len(m['values'])} different values")
            for v, where in m["values"].items():
                st.markdown(f"- `{v}`: {', '.join(where[:3])}{' …' if len(where) > 3 else ''}")
            st.caption("The tool does not pick one. Confirm the correct value with the applicant.")
with right:
    st.subheader("🏛 Zoning relief mentioned")
    rf = result.get("relief_found", {})
    st.markdown(f"Treated as relief requested: **{'Yes' if result['relief_requested'] else 'No'}** "
                "(change it in the sidebar)")
    for kind, hits in rf.items():
        with st.expander(f"{kind}: {len(hits)} mention(s)"):
            for h in hits[:3]:
                st.markdown(f"*{h['file']} p.{h['page']}*: …{h['snippet']}…")

# ---------------- rows ----------------
st.subheader("3 · Review each checklist item")
st.caption("Accept the suggestion if it's right, or reject it if the tool got it wrong. Only accepted rows go "
           "into the draft letter.")
filt = st.multiselect("Show", list(ICON.values()), default=[ICON[k] for k in ("missing", "unclear", "found", "staff_check")])
for r in rows:
    if ICON[r["status"]] not in filt:
        continue
    key = str(r["id"])
    d = decisions.get(key, {})
    mark = {"Accept": " · 👍 accepted", "Reject": " · 👎 rejected"}.get(d.get("decision"), "")
    with st.expander(f"{ICON[r['status']]}: item {r['id']}: {r['requirement'][:90]}{mark}"):
        st.markdown(f"**Requirement** ({r['cite']}): {r['requirement']}")
        st.markdown(f"**Why:** {r['reason']}")
        if r.get("evidence_quote"):
            badge = "🟢 quote verified on page" if r.get("verified") else "🟠 quote NOT found on cited page"
            st.markdown(f"**Evidence** ({r.get('file')} p.{r.get('page')}): “{r['evidence_quote']}”  \n{badge}"
                        + ("  \n🖼 cited page is scanned, so check it by eye" if r.get("scanned") else ""))
        if r.get("candidates"):
            st.caption("Pages searched: " + "; ".join(r["candidates"]))
        c1, c2 = st.columns([1, 2])
        choice = c1.radio("Planner decision", ["Undecided", "Accept", "Reject"], key=f"dec{key}",
                          index=["Undecided", "Accept", "Reject"].index(d.get("decision", "Undecided")),
                          horizontal=True)
        note = c2.text_input("Note (optional)", value=d.get("note", ""), key=f"note{key}")
        decisions[key] = {"decision": choice, "note": note}

# ---------------- output ----------------
st.subheader("4 · Draft letter")
reviewed = sum(v["decision"] != "Undecided" for v in decisions.values())
st.progress(reviewed / len(rows), text=f"{reviewed} of {len(rows)} items reviewed")


def letter() -> str:
    today = dt.date.today().isoformat()
    acc = [r for r in rows if decisions.get(str(r["id"]), {}).get("decision") == "Accept"]
    rej = [r for r in rows if decisions.get(str(r["id"]), {}).get("decision") == "Reject"]
    lines = [f"DRAFT FOR ADMINISTRATIVE OFFICER REVIEW, {today}", "",
             f"Re: completeness review, {cl['application_type']}, {cl['stage']}",
             f"Checklist: {cl['source_title']} (version {cl['source_version']})", ""]
    gaps = [r for r in acc if r["status"] in ("missing", "unclear")]
    if gaps:
        lines.append("The following items appear missing or incomplete:")
        for r in gaps:
            n = decisions[str(r["id"])]["note"]
            lines.append(f"- Item {r['id']} ({r['cite']}): {r['requirement']}" + (f" Note: {n}" if n else ""))
        lines.append("")
    staff = [r for r in acc if r["status"] == "staff_check"]
    if staff:
        lines.append("To be confirmed by staff (not visible in PDFs):")
        lines += [f"- Item {r['id']}: {r['requirement']}" for r in staff] + [""]
    if result["mismatches"]:
        lines.append("Please confirm these facts, which differ across documents:")
        lines += [f"- {m['fact']}: " + " vs ".join(m["values"]) for m in result["mismatches"]] + [""]
    ok = [r for r in acc if r["status"] == "found"]
    if ok:
        lines.append("Received: " + ", ".join(f"item {r['id']}" for r in ok) + ".")
    lines += ["", f"Tool suggestions rejected by reviewer: {len(rej)}. Undecided: {len(rows) - len(acc) - len(rej)}.",
              f"Prepared with an AI pre-check ({result['model']}). Every item above was reviewed by staff."]
    return "\n".join(lines)


txt = letter()
st.text_area("Preview", txt, height=260)
c1, c2 = st.columns(2)
c1.download_button("⬇️ Download draft letter", txt, file_name="completeness_draft.txt")
audit = {"result": result, "decisions": decisions, "exported": dt.datetime.now().isoformat()}
c2.download_button("⬇️ Download audit log (JSON)", json.dumps(audit, indent=2), file_name="review_audit.json")

with st.expander("Limits and equity"):
    st.markdown(
        "- **Scope:** one Providence checklist (version Dec 16, 2025). Other towns and stages aren't covered.\n"
        "- **Accuracy:** the AI can be wrong. Quotes are checked against the page in code; unverified quotes "
        "are marked 🟠 and \"found\" becomes \"unclear\".\n"
        "- **Scanned pages** have little text, so the tool may wrongly report items there as missing.\n"
        "- **Equity:** small and first-time applicants (nonprofits, homeowners adding units) lose the most to "
        "resubmission cycles, so they can run this pre-check before filing, at no cost. Hand-drawn or "
        "scanned packages are the most likely to be misread, so the tool never auto-rejects. A person decides.")
