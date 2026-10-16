# Cumberland Housing Approval Navigator: Housing Jam Q2

**Q2: What does it take to get approved?** Add one or more Cumberland, RI addresses. For each, the tool shows what the lot can hold and what it takes to get approved:

1. **Properties.** Add several addresses (or one of three real demo properties: R-3, A-2 and C-2) and compare them side by side. The zoning district, overlays and sewer planning area come from live state maps.
2. **What can be built.** Every housing type for that lot, ranked from easiest to hardest: ADU, two-family, 3+ units, commercial-to-housing and mixed-use. It shows the size and shape limits (setbacks, coverage, height, units by lot area) and cites the code section for each answer.
3. **Approval path.** Pick one and see whether it is a building permit only, a Zoning Officer modification, Minor Land Development, Unified Development Review or Major Land Development. You get a step-by-step timeline (who decides, legal time limits), estimated Planning fees and the next meeting dates.
4. **Checklist & notes.** The steps, the documents to prepare (from Cumberland's Appendix D, the modification rules and the variance standards), and a "confirm before you act" list, all with checkboxes. Each step has a notes box. Download the plan as text, or save all properties as a .json file and load it again later.
5. **Ask a question.** A chat box in the sidebar answers questions about the current property. It is grounded in verbatim code excerpts and the tool's own results, cites section numbers, and sends you to the right person at the town when it doesn't know.
6. **Who to call.** Planning, the Sewer Superintendent and the Building Official, with a pre-written email draft.

> **Not legal advice.** This gives a first answer to discuss with the Town of Cumberland Planning Department, which makes every decision. The rules were read on Oct 16, 2026 and are cited throughout.

## Run it

```bash
uv sync
uv run streamlit run app.py
```

The AI summary uses whichever model `.env` points to: `LLM_PROVIDER=anthropic|gemini|ollama`, plus `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY`. With no key, it uses local Ollama (`qwen3:8b`). The rule engine never needs AI.

## Who it's for

- **Users:** a small builder, a nonprofit project manager, or a homeowner adding a unit in Cumberland. All of them are often filing for the first time.
- **Why it's slow today:** the process is spread across three places:
  - the zoning ordinance (use table, lot-size table, ADU rules, modifications),
  - the land development regulations (minor vs. major review, deadlines),
  - a meeting-schedule PDF.

  The staff contacts are on a fourth page. Working out "which path am I on?" takes a consultant or several phone calls, and a wrong guess means a resubmission and a missed monthly meeting.
- **What this saves:** a first answer in about 2 minutes, with citations, plus the right person to call.

## How it works

`lookup.py` (address → zone), `process.py` (rules engine, options, fees, checklists), `chat.py` (grounded Q&A), `app.py` (UI). Data: `data/cumberland/rules.yaml` and `code_excerpts.md`.

```
address ─► RIGIS E-911 address point (building location, not street centerline)
        ─► RIGIS zoning layer (district) + overlays + sewered areas
        ─► process.py: RULES ENGINE (plain Python, no AI) reading data/cumberland/rules.yaml
              use table → lot-size math (§ 5-2) → ADU test (§ 6-6 / state law) →
              relief type (modification ≤15% / variance) → review path (thresholds in App. A)
        ─► steps + deadlines + contacts, every line with a citation
        ─► llm.py: AI rewrites the result in plain English + "questions to ask the planner"
              (it is given ONLY the engine's result; the UI says to trust the checks if they disagree)
        ─► person confirms: checklist + planner review box + downloadable summary
```

**Key AI-engineering ideas:**

- **The rules are data, not code.** `data/cumberland/rules.yaml` holds the use table, lot-size tables, ADU rules, thresholds, deadlines, schedule and contacts. Each entry has a citation. A planner can read and correct it, and another town is just a new YAML file.
- **The AI never decides.** Legal logic runs as deterministic, testable Python, and the model only explains it. This makes the tool safe to demo and easy to audit.
- **Model choice:** any hosted model or local Ollama works through `llm.py`. The explanation step is small, so a local 8B model is fine here.

## Data sources (read Oct 16, 2026)

| Data | Source |
|---|---|
| Address points (E-911, 2026) | RIGIS `FACILITY_AddressPoints_E911domains` |
| Zoning districts and overlays | RIGIS `RI_Zoning` FeatureServer (Cumberland source: eCode360, 2025-07-01) |
| Sewer service areas | RIGIS `UTILITY_Sewered_Areas_spf` |
| Use table (Supp 19, Apr 2026) | <https://ecode360.com/attachment/334062/CU3602-Ba%20Table%201%20Use%20Table.pdf> |
| Zoning ordinance (Appendix B): § 5-2, § 6-6, Art. 17, § 18-4 | <https://ecode360.com/30187156> |
| Land Development & Subdivision Regulations (Appendix A): § 2, § 5-E, § 5-G, § 5-H, § 5-I | <https://ecode360.com/30186134> |
| 2026 TRC / Planning Board schedule | <https://www.cumberlandri.gov/DocumentCenter/View/2407/2026-TRC-PB-Meeting-Schedule_V2> |
| Planning staff directory | <https://www.cumberlandri.gov/Directory.aspx?did=19> |
| State law: ADUs (§ 45-24-73), adaptive reuse (§ 45-24-37), substandard lots (§ 45-24-38) | rilegislature.gov |

## Limits (shown in the app)

- **Cumberland only.** The zoning map is the generalized statewide layer, so confirm on the official map.
- **You enter the lot size** (there is a link to the assessor). The tool does not check wetlands, flood zones, frontage or easements.
- **Recent changes may be missing.** Amendments adopted after Oct 16, 2026 aren't included, and eCode360 lists new ones (Ord. 26-17A, 26-22) that should be checked.
- **The 2026 schedule has no deadlines left**, and the 2027 schedule isn't posted yet, so the app says to call the Planning Clerk.

## Equity

Small and first-time builders can't pay a consultant just to learn the process. This gives them the same first map of it for free, plus a direct line to the planner. The tool never says "denied": every answer ends with a person at the town.

## Demo (2.5 minutes)

1. **Problem (20 s):** the process is spread across 3 documents and a staff page, and a wrong guess costs a month.
2. **Live run (90 s):**
   - Type `25 School St` and the app shows zone R-3 in the sewer area.
   - Choose 5 units with a 26,000 sq ft lot. The answer is "possible with a modification": 13.3% short, under the 15% limit.
   - Walk through the 7 steps with citations, then the contacts and the email draft.
3. **Contrast (20 s):** switch to a two-family home at `147 Pound Rd` (A-2). The answer is "not allowed", with alternatives to discuss.
4. **Responsible design (20 s):** the citations, the confirm checklist, the planner review box, and the downloaded summary.

## Earlier prototype

`check.py`, `extract.py` and `providence_checker_app.py` are a completeness checker for a Providence minor land development package (Q2 pack, Case A). Run it with `uv run streamlit run providence_checker_app.py`.
