"""Lotline: what can go on a lot in Cumberland, RI, and what it takes to get approved.

Sign in -> create a project (an address) -> explore what can be built -> plan the path -> start approval,
with a checklist where every item has notes and file uploads. Chat helper in the sidebar.
Run:  uv run streamlit run app.py
"""
import datetime as dt
from pathlib import Path
import hashlib
import html
import json
import urllib.parse

import streamlit as st

import chat
import checklist
import db
import llm
import lookup
import process

R = process.RULES
UTIL = {"both": "Public water and sewer", "one": "Only one of water or sewer", "none": "Neither (well and septic)"}
VERDICT_CLS = {"by_right": "v-ok", "conditional": "v-info", "needs_review": "v-info", "needs_modification": "v-info",
               "needs_relief": "v-warn", "not_allowed": "v-bad", "unknown": "v-warn"}
VERDICT_LABEL = {"by_right": "Allowed by right", "conditional": "By right, with a condition", "needs_review": "Allowed, needs plan review",
                 "needs_modification": "Small modification", "needs_relief": "Needs relief", "not_allowed": "Not allowed here",
                 "unknown": "Ask the town"}
CHECK_MARK = {"ok": "OK", "warn": "NOTE", "problem": "ISSUE", "unknown": "ASK", "info": "INFO"}
PRESETS = [  # real Cumberland addresses; lot sizes and uses from the town assessor database (read 2026-10-16)
    {"q": "25 School St", "lot_sf": 13502, "utilities": "both", "house_sf": 4192, "about": "R-3, four-family house from 1900, 0.31 acre"},
    {"q": "147 Pound Rd", "lot_sf": 622037, "utilities": "none", "house_sf": 2655, "about": "A-2, single family on well and septic, 14.28 acres"},
    {"q": "100 Mendon Rd", "lot_sf": 39640, "utilities": "both", "house_sf": 4089, "about": "C-2, commercial building, 0.91 acre"},
]

st.set_page_config(page_title="Lotline: Cumberland approvals", page_icon="▬", layout="wide", initial_sidebar_state="auto")

FONTS_CSS = (Path(__file__).parent / "static" / "fonts.css").read_text()
st.markdown("<style>" + FONTS_CSS + """

:root{--ground:#E6E5E1;--paper:#FBFBF9;--ink:#16202A;--ink2:#4B5762;--rule:#C8C6BE;
--orange:#F26A1B;--orange-t:#A8400A;--green:#2FA45B;--green-t:#1B6D3A;--blue:#1F66DB;--blue-t:#1648A0;--pink:#E0287D;--pink-t:#B01A62;--yellow:#F2C200;--red:#B3261E;
--display:'Big Shoulders Display','Arial Narrow',sans-serif;--body:'Public Sans',system-ui,sans-serif;--mono:'IBM Plex Mono',ui-monospace,monospace}
html,body,[data-testid="stApp"]{font-family:var(--body);color:var(--ink)}
.block-container{max-width:980px;padding-top:3.6rem;padding-bottom:5rem}
#MainMenu,footer,[data-testid="stToolbar"],[data-testid="stDecoration"]{visibility:hidden}
h1,h2,h3,h4{font-family:var(--display)!important;font-weight:800!important;letter-spacing:.005em;line-height:1.02}
h1{font-size:2.9rem!important}h2{font-size:2.1rem!important;margin-top:.3rem}h3{font-size:1.45rem!important}h4{font-size:1.15rem!important}
.stApp,.stApp p,.stApp li,.stApp label,.stApp button,.stApp input,.stApp textarea,.stApp a{font-family:var(--body)}
h1 *,h2 *,h3 *,h4 *{font-family:var(--display)!important}
div:has(>input),div:has(>textarea),[data-baseweb="select"]>div{background:#fff!important;border:1px solid var(--rule)!important}
div:has(>input):focus-within,div:has(>textarea):focus-within{border-color:var(--ink)!important}
a{color:var(--ink);text-decoration-thickness:1px;text-underline-offset:2px}
.paint{position:fixed;top:0;left:0;right:0;height:5px;display:flex;z-index:1000001}
.paint i{flex:1}.paint i:nth-child(1){background:var(--orange)}.paint i:nth-child(2){background:var(--green)}
.paint i:nth-child(3){background:var(--blue)}.paint i:nth-child(4){background:var(--pink)}.paint i:nth-child(5){background:var(--yellow)}
.mark{font-family:'Big Shoulders Stencil Display','Big Shoulders Display',sans-serif;font-weight:800;font-size:1.9rem;letter-spacing:.06em;line-height:1}
.lab{font:500 .68rem var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--ink2)}
.mono{font-family:var(--mono)}
.panel{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:1rem 1.15rem;margin:.5rem 0}
.tag{display:inline-block;font:500 .64rem var(--mono);letter-spacing:.08em;text-transform:uppercase;padding:.1rem .42rem;border:1px solid currentColor;border-radius:2px;margin-right:.35rem;white-space:nowrap}
.tag.town{color:var(--orange-t)}.tag.sewer{color:var(--green-t)}.tag.water{color:var(--blue-t)}.tag.plans{color:var(--pink-t)}.tag.form{color:var(--ink)}.tag.check{color:#7A5F00}
.tag.zone{background:var(--ink);color:#fff;border-color:var(--ink)}
.verdict{display:inline-block;font:700 .74rem var(--mono);letter-spacing:.07em;text-transform:uppercase;padding:.2rem .55rem;border-radius:2px;white-space:nowrap}
.v-ok{background:var(--ink);color:#fff}.v-info{border:1.5px solid var(--ink);color:var(--ink)}
.v-warn{background:var(--orange);color:var(--ink)}.v-bad{border:1.5px solid var(--red);color:var(--red);background:#fff}
.stButton>button,.stDownloadButton>button{border-radius:3px;font-weight:600;font-family:var(--body);box-shadow:none}
[data-testid="stBaseButton-secondary"]{background:var(--paper);border:1px solid var(--ink);color:var(--ink)}
[data-testid="stBaseButton-secondary"]:hover{background:var(--ink);color:#fff}
[data-testid="stBaseButton-primary"]{background:var(--ink);border:1px solid var(--ink)}
[data-testid="stBaseButton-primary"]:hover{background:#2D3C4B}
input,textarea,[data-baseweb="select"]>div{border-radius:3px!important}
[data-testid="stExpander"]{background:var(--paper);border:1px solid var(--rule);border-radius:3px}
[data-testid="stSidebar"]{background:var(--paper);border-right:1px solid var(--rule)}
[data-testid="stSidebar"] .block-container,[data-testid="stSidebarUserContent"]{padding-top:1.2rem}
[class*="st-key-item-"]{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.75rem 1rem .6rem;margin-bottom:.45rem;border-left-width:7px}
[class*="st-key-item-town"]{border-left-color:var(--orange)}[class*="st-key-item-sewer"]{border-left-color:var(--green)}
[class*="st-key-item-water"]{border-left-color:var(--blue)}[class*="st-key-item-plans"]{border-left-color:var(--pink)}
[class*="st-key-item-form"]{border-left-color:var(--ink)}[class*="st-key-item-check"]{border-left-color:var(--yellow)}
[class*="st-key-item-"] [data-testid="stCheckbox"] label p{font-weight:600;font-size:.98rem}
[class*="st-key-row-"],[class*="st-key-opt-"]{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.7rem 1rem;margin-bottom:.4rem}
.st-key-startbox{background:var(--paper);border:1px solid var(--rule);border-left:7px solid var(--orange);border-radius:3px;padding:.9rem 1.2rem 1.1rem;margin-top:1.4rem}
.st-key-authbox{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.6rem 1.1rem 1rem}
.hero{background:var(--paper);border:1px solid var(--rule);border-left:7px solid var(--ink);border-radius:3px;padding:1.1rem 1.3rem;margin:.6rem 0 1rem}
.hero h3{font-size:1.9rem!important;margin:.45rem 0 .8rem}
.hero .row{display:flex;gap:2rem;flex-wrap:wrap}.hero .row div{font-size:.92rem}
.hero .row .lab{display:block;margin-bottom:.1rem}
.crow{display:flex;gap:.8rem;padding:.55rem 0;border-bottom:1px solid var(--rule)}.crow:last-child{border-bottom:0}
.crow .st{flex:0 0 3.2rem;font:500 .66rem var(--mono);letter-spacing:.06em;padding-top:.2rem;color:var(--ink2)}
.crow .st.ISSUE{color:var(--red)}.crow .st.NOTE,.crow .st.ASK{color:var(--orange-t)}
.crow b{font-size:.93rem}.crow .d{font-size:.88rem}.crow .c{font:400 .72rem var(--mono);color:var(--ink2)}
.tl{margin:.5rem 0 1rem}
.tl .item{position:relative;padding:0 0 1rem 2.7rem}
.tl .item:before{content:"";position:absolute;left:.8rem;top:1.8rem;bottom:0;width:1px;background:var(--ink)}
.tl .item:last-child:before{display:none}
.tl .n{position:absolute;left:0;top:0;width:1.7rem;height:1.7rem;background:var(--ink);color:#fff;font:500 .78rem var(--mono);display:flex;align-items:center;justify-content:center}
.tl .body{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.65rem .9rem}
.tl .body p{margin:.25rem 0 0;font-size:.88rem}.meta{font:400 .74rem var(--mono);color:var(--ink2);margin:.2rem 0 0}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(8.5rem,1fr));gap:.5rem;margin:.4rem 0}
.facts div{background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.5rem .7rem}
.facts b{display:block;font:800 1.45rem var(--display);line-height:1.1}
.ledger .r{display:flex;justify-content:space-between;gap:1rem;padding:.4rem 0;border-bottom:1px dashed var(--rule);font-size:.9rem}
.ledger .r b,.ledger .r .amt{font-family:var(--mono)}.ledger .t{font-weight:700;border-bottom:0;border-top:1px solid var(--ink)}
.dates{display:flex;gap:.5rem;flex-wrap:wrap}.dates div{flex:1;min-width:9rem;background:var(--paper);border:1px solid var(--rule);border-radius:3px;padding:.55rem .8rem}
.dates b{display:block;font:800 1.35rem var(--display)}
.ai{background:var(--paper);border:1px solid var(--rule);border-left:7px solid var(--blue);border-radius:3px;padding:.8rem 1rem}
.contact{background:#F1F0EC;border:1px solid var(--rule);border-radius:3px;padding:.55rem .8rem;margin:.4rem 0;font-size:.88rem}
.contact .cn{font-weight:700}.contact .ct{color:var(--ink2);font-weight:400}.contact ul{margin:.3rem 0 0 1.1rem;padding:0}.contact li{margin:.1rem 0}
.bar{height:10px;background:#D6D4CD;border-radius:1px;overflow:hidden}.bar i{display:block;height:100%;background:var(--ink)}
.sw{display:inline-block;width:.8rem;height:.8rem;margin-right:.4rem;vertical-align:-1px}
.phase{display:flex;align-items:baseline;gap:.7rem;margin:1.6rem 0 .5rem;border-bottom:1px solid var(--ink);padding-bottom:.3rem}
.phase h3{margin:0}.phase .lab{margin-left:auto}
</style><div class="paint"><i></i><i></i><i></i><i></i><i></i></div>""", unsafe_allow_html=True)

ss = st.session_state
if not ss.get("user") and st.query_params.get("s"):
    ss.user = db.user_for_session(st.query_params.get("s"))
    if not ss.user:
        st.query_params.clear()
for k, v in {"user": None, "view": "projects", "pid": None, "pj": None, "pj_hash": "", "tab": "explore", "flash": None,
             "up_ver": {}}.items():
    ss.setdefault(k, v)


# ----------------------------------------------------------------- helpers
def esc(x) -> str:
    return html.escape(str(x))


def flash(kind: str, msg: str):
    ss.flash = (kind, msg)


@st.cache_data(show_spinner=False, ttl=3600)
def search(addr):
    return lookup.find_address(addr)


@st.cache_data(show_spinner=False, ttl=3600)
def facts(lon, lat):
    return lookup.site_facts(lon, lat)


@st.cache_data(show_spinner=False)
def explained(key, _res):
    return process.explain(_res)


def hash_of(pj: dict) -> str:
    return hashlib.sha1(json.dumps(pj, sort_keys=True, default=str).encode()).hexdigest()


def build_kw(pj: dict, choice: str | None = None) -> dict:
    choice, kw = choice or pj["choice"], pj["kw"]
    if choice == "adu":
        return dict(house_sf=pj["house_sf"] or None, adu_in_footprint=kw["adu_in_footprint"], adu_disability=kw["adu_disability"])
    if choice in ("multifamily", "reuse", "mixed_use"):
        return dict(units=int(kw["units"]), affordable=kw["affordable"], new_street=kw["new_street"])
    return {}


def res_for(pj: dict):
    if not pj.get("choice"):
        return None
    return process.evaluate(pj["site"], pj["choice"], pj["lot_sf"] or None, pj["utilities"], **build_kw(pj))


def opts_for(pj: dict):
    return process.options(pj["site"], pj["lot_sf"] or None, pj["utilities"], pj["house_sf"] or None)


def progress_for(pj: dict) -> tuple[int, int]:
    res = res_for(pj)
    return checklist.progress(pj, checklist.build(pj, res)) if res and pj.get("stage") == "approval" else (0, 0)


def new_project_data(name: str, m: dict, site: dict, **over) -> dict:
    d = {"name": name, "address": m["address"], "zip": m["zip"], "lon": m["lon"], "lat": m["lat"], "site": site, "lot_sf": 0,
         "utilities": "both" if site["sewer_area"] else "none", "house_sf": 0, "choice": None, "stage": "explore",
         "kw": {"units": 5, "affordable": False, "new_street": False, "adu_in_footprint": False, "adu_disability": False},
         "done": {}, "notes": {}, "general_notes": "", "chat": [], "review": {"name": "", "verdict": "Not reviewed", "notes": ""},
         "created": str(dt.date.today())}
    d.update(over)
    return d


def open_project(pid: int, tab: str | None = None):
    pj = db.get_project(ss.user["id"], pid)
    if not pj:
        flash("error", "That project wasn't found.")
        return
    ss.pid, ss.pj, ss.view = pid, pj, "project"
    ss.pj_hash = hash_of(pj)
    ss.tab = tab or ("approval" if pj.get("stage") == "approval" else "explore")


def go_tab(t: str):
    ss.tab = t


def go_projects():
    ss.view, ss.pid, ss.pj = "projects", None, None


def create_project_from_form():
    name = (ss.get("np_name") or "").strip()
    q = (ss.get("np_addr") or "").strip()
    if not q:
        flash("warning", "Enter the property address.")
        return
    try:
        matches = search(q)
    except Exception as e:
        flash("error", f"Address search failed ({type(e).__name__}). Check your connection.")
        return
    if not matches:
        flash("warning", "No Cumberland address found. Use the house number and street, e.g. “25 School St”.")
        return
    m = next((x for x in matches if x["address"] == ss.get("np_pick")), matches[0])
    make_project(name or m["address"].title(), m)
    ss.np_name = ss.np_addr = ""


def make_project(name: str, m: dict, **over):
    pj = new_project_data(name, m, facts(m["lon"], m["lat"]), **over)
    open_project(db.create_project(ss.user["id"], pj), "explore")


def make_preset(i: int):
    pr = PRESETS[i]
    m = search(pr["q"])[0]
    make_project(m["address"].title(), m, lot_sf=pr["lot_sf"], utilities=pr["utilities"], house_sf=pr["house_sf"])


def choose(choice: str):
    pj = ss.pj
    pj["choice"] = choice
    if choice == "adu":
        o = next((x for x in opts_for(pj) if x["project"] == "adu"), None)
        if o and o["verdict"] == "conditional":
            pj["kw"]["adu_in_footprint"] = True
    ss.tab = "path"


def start_approval():
    ss.pj["stage"] = "approval"
    ss.pj["started"] = str(dt.date.today())
    ss.tab = "approval"


def sign_in(user: dict):
    ss.user = user
    st.query_params["s"] = db.create_session(user["id"])


def sign_out():
    tok = st.query_params.get("s")
    if tok:
        db.delete_session(tok)
    st.query_params.clear()
    for k in list(ss.keys()):
        del ss[k]


def verdict_tag(v: str) -> str:
    return f'<span class="verdict {VERDICT_CLS.get(v, "v-info")}">{esc(VERDICT_LABEL.get(v, v))}</span>'


def tel(phone: str) -> str:
    digits = "".join(ch for ch in phone.split("ext.")[0] if ch.isdigit())
    ext = phone.split("ext.")[1].strip() if "ext." in phone else ""
    return "tel:+1" + digits + ("," + ext if ext else "")


# ----------------------------------------------------------------- signed-out screen
def auth_screen():
    left, right = st.columns([1.25, 1], gap="large")
    with left:
        st.markdown('<div class="mark">LOTLINE</div><div class="lab" style="margin-bottom:2.2rem">Cumberland, Rhode Island</div>', unsafe_allow_html=True)
        st.markdown("# Know what fits on the lot, and who to call.")
        st.markdown("<p style='font-size:1.05rem;max-width:34rem'>Add a property, see what it can legally become, then work through the "
                    "approval one call, one document and one note at a time. Built for small builders, nonprofits and homeowners who "
                    "can't pay a consultant to read the code first.</p>", unsafe_allow_html=True)
        st.markdown('<div class="lab" style="margin:1.6rem 0 .4rem">Checklist colors, borrowed from the paint locators spray before anyone digs</div>'
                    '<div style="font-size:.9rem;line-height:1.9">'
                    '<span class="sw" style="background:var(--orange)"></span>Orange: call the town<br>'
                    '<span class="sw" style="background:var(--green)"></span>Green: sewer<br>'
                    '<span class="sw" style="background:var(--blue)"></span>Blue: water<br>'
                    '<span class="sw" style="background:var(--pink)"></span>Pink: plans and survey<br>'
                    '<span class="sw" style="background:var(--yellow)"></span>Yellow: double-check</div>', unsafe_allow_html=True)
    with right:
        authbox = st.container(key="authbox")
        t_in, t_up = authbox.tabs(["Sign in", "Create account"])
        with t_in:
            with st.form("signin"):
                em = st.text_input("Email", key="si_email")
                pw = st.text_input("Password", type="password", key="si_pw")
                if st.form_submit_button("Sign in", type="primary", use_container_width=True):
                    user, err = db.authenticate(em, pw)
                    if user:
                        sign_in(user)
                        st.rerun()
                    st.error(err)
        with t_up:
            with st.form("signup"):
                nm = st.text_input("Your name", key="su_name")
                em2 = st.text_input("Email", key="su_email")
                pw2 = st.text_input("Password (8 or more characters)", type="password", key="su_pw")
                if st.form_submit_button("Create account", type="primary", use_container_width=True):
                    user, err = db.create_user(em2, nm, pw2)
                    if user:
                        sign_in(user)
                        st.rerun()
                    st.error(err)
        if st.button("Try it without an account", use_container_width=True):
            sign_in(db.create_demo_user())
            st.rerun()
        st.caption("Projects, notes and uploaded files are stored on this server, tied to your account. Passwords are hashed. "
                   "This is a demo, so don't upload anything sensitive.")


# ----------------------------------------------------------------- signed-in screens
def topbar():
    a, b, c = st.columns([5, 1.2, 1.2], vertical_alignment="center")
    a.markdown(f'<span class="mark">LOTLINE</span> <span class="lab" style="margin-left:.8rem">Cumberland approvals · {esc(ss.user["name"])}</span>',
               unsafe_allow_html=True)
    b.button("Projects", on_click=go_projects, use_container_width=True)
    c.button("Sign out", on_click=sign_out, use_container_width=True)
    if ss.flash:
        kind, msg = ss.flash
        getattr(st, kind)(msg)
        ss.flash = None


def projects_view():
    projects = db.list_projects(ss.user["id"])
    st.markdown("# Your projects")
    st.caption("One project per property. Each keeps its own checklist, notes, files and chat.")
    with st.expander("Start a new project", expanded=not projects):
        c1, c2 = st.columns(2)
        c1.text_input("Project name", key="np_name", placeholder="e.g. School St fourplex")
        c2.text_input("Property address in Cumberland", key="np_addr", placeholder="e.g. 25 School St")
        q = (ss.get("np_addr") or "").strip()
        matches = []
        if q:
            try:
                matches = search(q)
            except Exception:
                matches = []
            if len(matches) > 1:
                st.selectbox("Which address?", [x["address"] for x in matches], key="np_pick")
        st.button("Create project", type="primary", on_click=create_project_from_form)
        st.markdown('<div class="lab" style="margin:.8rem 0 .3rem">Or start from a real Cumberland property</div>', unsafe_allow_html=True)
        cols = st.columns(len(PRESETS))
        for i, (col, pr) in enumerate(zip(cols, PRESETS)):
            col.button(pr["q"], key=f"preset{i}", on_click=make_preset, args=(i,), use_container_width=True)
            col.caption(pr["about"])
    if not projects:
        st.markdown('<div class="panel"><b>No projects yet.</b> Start one above. Add the address and you will see what the lot can hold '
                    'before you spend anything.</div>', unsafe_allow_html=True)
        return
    h = st.columns([4, 1.4, 2.2, 1.1, 0.9])
    for col, t in zip(h, ["Project", "Zone", "Stage", "", ""]):
        col.markdown(f'<span class="lab">{t}</span>', unsafe_allow_html=True)
    for p in projects:
        d = p["data"]
        done, total = progress_for(d)
        with st.container(key=f"row-{p['id']}"):
            c = st.columns([4, 1.4, 2.2, 1.1, 0.9], vertical_alignment="center")
            c[0].markdown(f"**{esc(p['name'])}**<br><span class='mono' style='font-size:.78rem;color:var(--ink2)'>{esc(p['address'])} · {d['lot_sf']:,} sq ft</span>",
                          unsafe_allow_html=True)
            c[1].markdown(f'<span class="tag zone">{esc(d["site"]["zone"])}</span>', unsafe_allow_html=True)
            if p["stage"] == "approval":
                pct = int(done / max(total, 1) * 100)
                c[2].markdown(f'<span class="tag form">In approval</span><div class="bar" style="margin-top:.35rem"><i style="width:{pct}%"></i></div>'
                              f'<span class="meta">{done} of {total} items</span>', unsafe_allow_html=True)
            else:
                c[2].markdown('<span class="tag check">Exploring</span>' + (f'<br><span class="meta">{esc(process.PROJECTS[d["choice"]])}</span>' if d.get("choice") else ""),
                              unsafe_allow_html=True)
            c[3].button("Open", key=f"open{p['id']}", on_click=open_project, args=(p["id"],), type="primary", use_container_width=True)
            with c[4].popover("⋯", use_container_width=True):
                st.caption("Delete this project, its notes and its files?")
                st.button("Delete", key=f"del{p['id']}", on_click=db.delete_project, args=(ss.user["id"], p["id"]))


def project_header(pj: dict):
    st.markdown(f'<div class="lab">Project</div>', unsafe_allow_html=True)
    st.markdown(f"# {esc(pj['name'])}")
    stage = '<span class="tag form">In approval</span>' if pj["stage"] == "approval" else '<span class="tag check">Exploring</span>'
    ov = "".join(f'<span class="tag town">{esc(o)}</span>' for o in pj["site"]["overlays"])
    st.markdown(f'<span class="mono" style="font-size:.85rem">{esc(pj["address"])}, Cumberland RI {esc(pj["zip"])}</span> &nbsp; '
                f'<span class="tag zone">Zone {esc(pj["site"]["zone"])}</span>{stage}{ov}', unsafe_allow_html=True)
    st.write("")
    a, b, c = st.columns(3)
    for col, key, label, off in [(a, "explore", "1  What can be built", False), (b, "path", "2  Plan the path", False),
                                 (c, "approval", "3  Approval checklist", pj["stage"] != "approval")]:
        col.button(label if not off else label + " (not started)", key=f"nav_{key}", on_click=go_tab, args=(key,), disabled=off,
                   type="primary" if ss.tab == key else "secondary", use_container_width=True)


# ---- tab 1
def tab_explore(pj: dict):
    site = pj["site"]
    st.markdown("## What can be built here?")
    st.markdown("<div class='lab'>The lot</div>", unsafe_allow_html=True)
    a, b, c = st.columns(3)
    pj["lot_sf"] = int(a.number_input("Lot size (sq ft)", min_value=0, step=500, value=int(pj["lot_sf"]), key=f"lot{ss.pid}",
                                      help="From the town assessor database."))
    keys = list(UTIL)
    pj["utilities"] = b.selectbox("Utilities", keys, index=keys.index(pj["utilities"]), format_func=UTIL.get, key=f"ut{ss.pid}",
                                  help="Pre-filled from the sewer planning map. You'll confirm it with the town and the water supplier.")
    pj["house_sf"] = int(c.number_input("Existing building (sq ft)", min_value=0, step=100, value=int(pj["house_sf"]), key=f"hs{ss.pid}",
                                        help="Used for the ADU size limit (60% of the main house)."))
    sup = " and ".join(site.get("water_suppliers") or []) or "none found"
    st.caption(f"Look up lot size and building area in the [town assessor database]({R['sources']['assessor']['url']}). "
               f"Sewer: {'inside' if site['sewer_area'] else 'outside'} a state sewer planning area (about 2012, so sewer is likely nearby, not confirmed). "
               f"Water supplier area on the state map: {sup}.")

    sr = process.site_rules(site, pj["lot_sf"] or None, pj["utilities"])
    if sr:
        mu = sr["max_units_by_area"]
        st.markdown(f"<div class='lab' style='margin-top:.8rem'>Size and shape limits in {esc(site['zone'])}</div>", unsafe_allow_html=True)
        st.markdown('<div class="facts">'
                    f'<div class="lab">Min lot<b>{sr["min_lot"]:,} sq ft</b></div>'
                    f'<div class="lab">Setbacks front / side / rear<b>{sr["front"]} / {sr["side"]} / {sr["rear"]} ft</b></div>'
                    f'<div class="lab">Max coverage<b>{sr["coverage"]:.0%}' + (f' · {sr["max_footprint"]:,} sq ft' if sr["max_footprint"] else '') + '</b></div>'
                    f'<div class="lab">Max height<b>{sr["height"]} ft</b></div>'
                    f'<div class="lab">Min frontage<b>{sr["frontage"]} ft</b></div>'
                    + (f'<div class="lab">Units by lot area<b>{mu}</b></div>' if mu is not None else '') + '</div>', unsafe_allow_html=True)
        st.caption(f"Source: {sr['cite']}. Units by lot area = first unit's area plus the per-unit increment, before any relief.")
    else:
        st.caption("This zone's dimensional table isn't encoded here (commercial and industrial zones, § 5-3). Ask Planning.")

    st.markdown("### Everything this lot could become")
    st.caption("Easiest first. Choose one to see what approval would take.")
    for i, o in enumerate(opts_for(pj)):
        with st.container(key=f"opt-{i}"):
            left, right = st.columns([3.2, 1.3], vertical_alignment="center")
            left.markdown(f"<span style='font:800 1.35rem var(--display)'>{esc(o['title'])}</span><br>{esc(o['headline'])}"
                          f"<br><span style='font-size:.84rem;color:var(--ink2)'>{esc(o['detail'])}</span>", unsafe_allow_html=True)
            right.markdown(verdict_tag(o["verdict"]), unsafe_allow_html=True)
            right.button("See what it takes" if o["verdict"] == "not_allowed" else "Plan this", key=f"pick{i}", on_click=choose,
                         args=(o["project"],), type="secondary" if o["verdict"] == "not_allowed" else "primary", use_container_width=True)


# ---- tab 2
def tab_path(pj: dict):
    site = pj["site"]
    if not pj.get("choice"):
        pj["choice"] = opts_for(pj)[0]["project"]
    st.markdown("## Plan the path")
    plist = list(process.PROJECTS)
    pj["choice"] = st.selectbox("Project type", plist, index=plist.index(pj["choice"]), format_func=process.PROJECTS.get, key=f"proj{ss.pid}")
    with st.expander("Adjust assumptions"):
        kw = pj["kw"]
        if pj["choice"] == "adu":
            x, y = st.columns(2)
            kw["adu_in_footprint"] = x.checkbox("ADU inside the existing house or garage footprint", value=kw["adu_in_footprint"], key=f"fp{ss.pid}")
            kw["adu_disability"] = y.checkbox("Owner-occupied, for a family member with a disability", value=kw["adu_disability"], key=f"dis{ss.pid}")
        elif pj["choice"] in ("multifamily", "reuse", "mixed_use"):
            x, y = st.columns(2)
            minu = 3 if pj["choice"] == "multifamily" else 1
            kw["units"] = int(x.number_input("Number of units", min_value=minu, value=max(int(kw["units"]), minu), key=f"un{ss.pid}"))
            kw["affordable"] = y.checkbox("Some units are income-restricted", value=kw["affordable"], key=f"af{ss.pid}")
            kw["new_street"] = y.checkbox("Project creates or extends a street", value=kw["new_street"], key=f"ns{ss.pid}")
        else:
            st.caption("No extra assumptions for this type. Lot size and utilities are on step 1.")
        st.caption(f"Lot {pj['lot_sf']:,} sq ft · {UTIL[pj['utilities']]}.")

    res = res_for(pj)
    cls = VERDICT_CLS.get(res["verdict"], "v-info")
    border = {"v-ok": "var(--ink)", "v-info": "var(--ink)", "v-warn": "var(--orange)", "v-bad": "var(--red)"}[cls]
    st.markdown(f'<div class="hero" style="border-left-color:{border}">{verdict_tag(res["verdict"])}<h3>{esc(res["headline"])}</h3>'
                f'<div class="row"><div><span class="lab">Path</span>{esc(res["path"])}</div>'
                f'<div><span class="lab">Decided by</span>{esc(res["decided_by"])}</div></div></div>', unsafe_allow_html=True)

    st.markdown("### What we checked")
    rows = "".join(f'<div class="crow"><div class="st {CHECK_MARK[c["result"]]}">{CHECK_MARK[c["result"]]}</div>'
                   f'<div><b>{esc(c["label"])}</b><div class="d">{esc(c["detail"])}</div><div class="c">{esc(c["cite"])}</div></div></div>'
                   for c in res["checks"])
    st.markdown(f'<div class="panel">{rows}</div>', unsafe_allow_html=True)
    if res["relief"]:
        st.markdown("**Relief you would need:** " + " · ".join(esc(r) for r in res["relief"]), unsafe_allow_html=True)

    st.markdown("### Steps, in order")
    items = "".join(
        f'<div class="item"><span class="n">{i}</span><div class="body"><b>{esc(s["title"])}</b> <span class="tag town" style="margin-left:.4rem">{esc(s["who"])}</span>'
        f'<p>{esc(s["what"])}</p>' + (f'<p class="meta">Time: {esc(s["time"])}</p>' if s["time"] else "")
        + (f'<p class="meta">Source: {esc(s["cite"])}</p>' if s["cite"] else "") + '</div></div>' for i, s in enumerate(res["steps"], 1))
    st.markdown(f'<div class="tl">{items}</div>', unsafe_allow_html=True)

    fe = process.fee_estimate(res)
    st.markdown("### Fees and dates")
    c1, c2 = st.columns(2)
    with c1:
        lines = "".join(f'<div class="r"><span>{esc(l["item"])} <span class="meta">{esc(l["formula"])}</span></span><span class="amt">${l["amount"]:,}</span></div>' for l in fe["lines"])
        if lines:
            lines += f'<div class="r t"><span>Planning fees, estimated</span><span class="amt">${fe["total"]:,}</span></div>'
        other = "".join(f'<p class="meta" style="margin:.35rem 0 0">{esc(o)}</p>' for o in fe["other"])
        st.markdown(f'<div class="panel ledger"><div class="lab">Estimated fees</div>{lines}{other}</div>', unsafe_allow_html=True)
        st.caption(f"Source: {fe['cite']}. {fe['note']}")
    with c2:
        d = res["dates"]
        if d["deadline"]:
            tiles = (f'<div class="lab">Filing deadline<b>{esc(d["deadline"])}</b></div><div class="lab">Technical Review · 3:00 pm<b>{esc(d["trc"])}</b></div>'
                     f'<div class="lab">Planning Board · 6:30 pm<b>{esc(d["planning_board"])}</b></div>')
        else:
            tiles = '<div class="lab">Next filing deadline<b>Call the Clerk</b></div>' + "".join(
                f'<div class="lab">Planning Board · 6:30 pm<b>{esc(x)}</b></div>' for x in d.get("upcoming_pb", []))
        st.markdown(f'<div class="lab" style="margin-bottom:.3rem">Dates</div><div class="dates">{tiles}</div>', unsafe_allow_html=True)
        st.caption(d["note"] + " " + R["schedule_2026"]["zoning_only"])

    st.markdown("### In plain English")
    with st.spinner("Writing a plain-English summary…"):
        ex = explained(repr((pj["address"], pj["choice"], pj["lot_sf"], pj["utilities"], sorted(build_kw(pj).items()))), res)
    q_html = "".join(f"<li>{esc(q)}</li>" for q in ex.get("questions_for_planner", []))
    st.markdown(f'<div class="ai">{esc(ex.get("summary", ""))}'
                + (f'<div class="lab" style="margin:.7rem 0 .2rem">Questions to ask the planner</div><ul style="margin:.2rem 0 0 1.1rem">{q_html}</ul>' if q_html else "") + '</div>',
                unsafe_allow_html=True)
    st.caption(f"Written by AI ({llm.provider()}:{llm.model_name()}) from the checks above. If it disagrees with them, trust the checks and their sources.")

    with st.container(key="startbox"):
        if pj["stage"] == "approval":
            st.markdown("### Approval is under way")
            st.write("Your checklist is ready. Changing the project type here changes which items appear.")
            st.button("Open the checklist", type="primary", on_click=go_tab, args=("approval",))
        else:
            st.markdown("### Ready to go ahead?")
            st.write("Starting approval builds your checklist: the calls to make, the documents to gather and the steps to file. "
                     "Every item gets a notes box and a place to upload files. You can come back to this screen at any time.")
            st.button("Start approval for this project", type="primary", on_click=start_approval)


# ---- tab 3
def contact_block(it: dict, pj: dict) -> str:
    c = it["contact"]
    if not c:
        return ""
    bits = []
    if c.get("phone"):
        bits.append(f'<a href="{esc(tel(c["phone"]))}">{esc(c["phone"])}</a>')
    if c.get("email"):
        body = (f"Hello,\n\nI'm working on a project at {pj['address']}, Cumberland (zone {pj['site']['zone']}): "
                f"{process.PROJECTS[pj['choice']].lower()}.\n\n" + ("I have a few questions:\n" + "\n".join(f"- {a}" for a in it["asks"]) if it["asks"] else "")
                + "\n\nThank you,\n" + ss.user["name"])
        href = "mailto:" + c["email"] + "?" + urllib.parse.urlencode({"subject": f"Question about {pj['address']}", "body": body}, quote_via=urllib.parse.quote)
        bits.append(f'<a href="{esc(href)}">{esc(c["email"])}</a>')
    asks = "".join(f"<li>{esc(a)}</li>" for a in it["asks"])
    return (f'<div class="contact"><div class="cn">{esc(c["name"])} <span class="ct">{esc(c.get("title", ""))}</span></div>'
            f'<div>{" · ".join(bits)}</div>' + (f'<div class="meta">{esc(c["extra"])}</div>' if c.get("extra") else "")
            + (f'<div class="lab" style="margin-top:.4rem">Ask</div><ul>{asks}</ul>' if asks else "") + "</div>")


def render_item(pj: dict, it: dict, n: int, uploads: dict, compact: bool = False):
    k, uid, pid = it["key"], ss.user["id"], ss.pid
    files = uploads.get(k, [])
    has_note = bool(pj["notes"].get(k))
    with st.container(key=f"item-{it['kind']}-{n}"):
        pj["done"][k] = st.checkbox(it["title"], value=bool(pj["done"].get(k)), key=f"ck|{pid}|{k}")
        meta = f'<span class="tag {it["kind"]}">{esc(checklist.KINDS[it["kind"]])}</span>'
        if it["who"] and not compact:
            meta += f'<span class="meta">{esc(it["who"])}</span> '
        if it["cite"]:
            meta += f'<span class="meta">Source: {esc(it["cite"])}</span>'
        st.markdown(meta, unsafe_allow_html=True)
        if it["detail"]:
            st.markdown(f"<div style='font-size:.9rem;margin:.3rem 0'>{esc(it['detail'])}</div>", unsafe_allow_html=True)
        if it["time"]:
            st.markdown(f'<div class="meta">Time: {esc(it["time"])}</div>', unsafe_allow_html=True)
        if it["contact"]:
            st.markdown(contact_block(it, pj), unsafe_allow_html=True)
        badge = " · ".join(x for x in [("note" if has_note else ""), (f"{len(files)} file{'s' if len(files) != 1 else ''}" if files else "")] if x)
        if st.toggle("Notes and files" + (f"  ·  {badge}" if badge else ""), value=bool(badge), key=f"tg|{pid}|{k}"):
            pj["notes"][k] = st.text_area("Notes", value=pj["notes"].get(k, ""), key=f"nt|{pid}|{k}", height=90,
                                          placeholder="Who you spoke to, the date, what they said, what happens next.")
            ver = ss.up_ver.get(k, 0)
            up = st.file_uploader(f"Add a file. Suggested: {it['expect'] or 'any related document'}", accept_multiple_files=True,
                                  key=f"up|{pid}|{k}|{ver}", help="PDF, images, Word, Excel, text or CAD files up to 10 MB each. Stored on this server for this project only.")
            if up:
                for f in up:
                    ok, msg = db.add_upload(uid, pid, k, f.name, f.getvalue())
                    if not ok:
                        flash("error", msg)
                ss.up_ver[k] = ver + 1
                st.rerun()
            for u in files:
                a, b, c = st.columns([5, 1.3, 1.1], vertical_alignment="center")
                a.markdown(f"<span class='mono' style='font-size:.8rem'>{esc(u['filename'])}</span> <span class='meta'>{u['size'] // 1024} KB</span>", unsafe_allow_html=True)
                data = db.read_upload(uid, pid, u["id"])
                if data is not None:
                    b.download_button("Download", data, file_name=u["filename"], key=f"dl|{u['id']}", use_container_width=True)
                c.button("Remove", key=f"rm|{u['id']}", on_click=db.delete_upload, args=(uid, pid, u["id"]), use_container_width=True)


def tab_approval(pj: dict):
    res = res_for(pj)
    items = checklist.build(pj, res)
    uploads = db.list_uploads(ss.user["id"], ss.pid)
    done, total = checklist.progress(pj, items)
    st.markdown("## Approval checklist")
    st.caption(f"{process.PROJECTS[pj['choice']]} · {res['path']} · decided by {res['decided_by']}")
    nxt = next((i for i in items if not pj["done"].get(i["key"]) and i["phase"] in ("talk", "check")), None) or next((i for i in items if not pj["done"].get(i["key"])), None)
    pct = int(done / max(total, 1) * 100)
    st.markdown(f'<div class="panel"><div style="display:flex;justify-content:space-between;align-items:baseline"><span style="font:800 1.6rem var(--display)">{done} of {total} done</span>'
                f'<span class="lab">{pct}%</span></div><div class="bar" style="margin:.4rem 0 .6rem"><i style="width:{pct}%"></i></div>'
                + (f'<div class="lab">Next up</div><div style="font-weight:600">{esc(nxt["title"])}</div>' if nxt else '<b>Everything on the list is done.</b>') + '</div>', unsafe_allow_html=True)

    n = 0
    for phase, label in checklist.PHASES:
        group = [i for i in items if i["phase"] == phase]
        if not group:
            continue
        d_ = sum(bool(pj["done"].get(i["key"])) for i in group)
        st.markdown(f'<div class="phase"><h3>{esc(label)}</h3><span class="lab">{d_} of {len(group)}</span></div>', unsafe_allow_html=True)
        if phase == "docs":
            st.caption(R["documents"]["note"])
            by_group: dict[str, list[dict]] = {}
            for i in group:
                by_group.setdefault(i["who"], []).append(i)
            for gi, (gname, gitems) in enumerate(by_group.items()):
                gd = sum(bool(pj["done"].get(i["key"])) for i in gitems)
                with st.expander(f"{gname}  ·  {gd} of {len(gitems)}", expanded=False):
                    for i in gitems:
                        n += 1
                        render_item(pj, i, n, uploads, compact=True)
        else:
            for i in group:
                n += 1
                render_item(pj, i, n, uploads)

    st.markdown('<div class="phase"><h3>General notes</h3></div>', unsafe_allow_html=True)
    pj["general_notes"] = st.text_area("General notes", value=pj["general_notes"], key=f"gn|{ss.pid}", height=130,
                                       placeholder="Neighbors, costs, anything to remember.", label_visibility="collapsed")
    with st.expander("Planner review (for town staff)"):
        rv = pj["review"]
        rv["name"] = st.text_input("Reviewer name", value=rv["name"], key=f"rvn|{ss.pid}")
        opts_rv = ["Not reviewed", "Correct", "Needs changes"]
        rv["verdict"] = st.radio("Is the path above correct?", opts_rv, index=opts_rv.index(rv["verdict"]), horizontal=True, key=f"rvv|{ss.pid}")
        rv["notes"] = st.text_area("Corrections or notes for the applicant", value=rv["notes"], key=f"rvt|{ss.pid}")

    def summary() -> str:
        L = [f"APPROVAL FILE: {pj['name']} · {pj['address']}, Cumberland RI · {dt.date.today()}",
             "Prepared with an AI-assisted tool. Not legal advice; the Town of Cumberland decides.", "",
             f"Zone {pj['site']['zone']} · lot {pj['lot_sf']:,} sq ft · {UTIL[pj['utilities']]}", f"Project: {res['project']} · {res['headline']}",
             f"Path: {res['path']} · decided by {res['decided_by']}"]
        for phase, label in checklist.PHASES:
            g = [i for i in items if i["phase"] == phase]
            if not g:
                continue
            L += ["", label.upper()]
            for i in g:
                L.append(f"[{'x' if pj['done'].get(i['key']) else ' '}] {i['title']}" + (f"  [{i['cite']}]" if i["cite"] else ""))
                if i["contact"]:
                    L.append(f"      Contact: {i['contact']['name']} {i['contact'].get('phone', '')} {i['contact'].get('email', '')}".rstrip())
                if pj["notes"].get(i["key"]):
                    L.append(f"      Notes: {pj['notes'][i['key']]}")
                for u in uploads.get(i["key"], []):
                    L.append(f"      File: {u['filename']}")
        if pj["general_notes"]:
            L += ["", "GENERAL NOTES", pj["general_notes"]]
        L += ["", "SOURCES", *[f"- {x['title']}: {x['url']}" for x in R["sources"].values()]]
        return "\n".join(L)

    st.download_button("Download the checklist with notes (.txt)", summary(), file_name=f"approval_file_{pj['address'].replace(' ', '_')}.txt")


# ----------------------------------------------------------------- footer + chat
def footer():
    st.markdown("---")
    with st.expander("Sources, limits and privacy"):
        st.markdown("\n".join(f"- [{x['title']}]({x['url']})" for x in R["sources"].values()))
        st.markdown("**Limits.** Cumberland only. The zoning map is the generalized statewide layer. It does not check wetlands, flood zones, "
                    "frontage, easements, or amendments adopted after the rules were read. Commercial-zone dimensions aren't encoded. Lot size comes "
                    "from you, the sewer indicator is a planning map, and the fee table is the posted 2015 schedule.")
        st.markdown("**Equity.** Small builders, homeowners adding an ADU and first-time nonprofit developers can't pay a consultant just to learn the "
                    "process. This gives them the same first map for free, plus a direct line to the planner. The tool never says “denied”: "
                    "every answer ends with a person at the town.")
        st.markdown("**Privacy.** Accounts, notes and files are stored on the server running this app, tied to your account. Passwords are hashed. "
                    "This is a demo, so don't upload sensitive documents.")
    st.caption("A first answer to discuss with the Town of Cumberland. Not a decision and not legal advice. The Planning Department makes the call.")


def sidebar_chat(pj: dict):
    with st.sidebar:
        st.markdown('<div class="lab">Ask a question</div>', unsafe_allow_html=True)
        st.markdown(f"### {esc(pj['name'])}")
        st.caption("Answers come from the town code and this project's results, with section numbers.")
        box = st.container(height=360, border=True)
        with box:
            if not pj["chat"]:
                st.caption("Pick a question below, or type your own.")
            for m in pj["chat"]:
                with st.chat_message(m["role"]):
                    st.markdown(m["content"])
        starters = chat.STARTERS.get(pj.get("choice") or "multifamily", chat.STARTERS["multifamily"])
        for i, sq in enumerate(starters):
            st.button(sq, key=f"starter{i}", on_click=lambda s=sq: ss.__setitem__("pending_q", s), use_container_width=True)
        question = st.chat_input("Ask about this project…") or ss.pop("pending_q", None)
        if question:
            res_c = res_for(pj)
            dn, tt = progress_for(pj)
            state = chat.property_summary({**pj, "project": pj.get("choice")}, res_c, opts_for(pj), f"{dn} of {tt} items done" if tt else "approval not started",
                                          "\n".join(v for v in [pj.get("general_notes", ""), *pj["notes"].values()] if v))
            with box:
                with st.chat_message("user"):
                    st.markdown(question)
                with st.chat_message("assistant"):
                    with st.spinner("Checking the town's code…"):
                        reply = chat.ask(question, pj["chat"], state)
                    st.markdown(reply)
            pj["chat"] += [{"role": "user", "content": question}, {"role": "assistant", "content": reply}]
        if pj["chat"]:
            st.button("Clear chat", on_click=lambda: pj.__setitem__("chat", []), use_container_width=True)
        st.caption("AI can be wrong. It cites the code and points you to the town when it doesn't know. Not legal advice.")


# ----------------------------------------------------------------- main
if not ss.user:
    auth_screen()
else:
    topbar()
    if ss.view == "projects" or not ss.pj:
        projects_view()
    else:
        pj = ss.pj
        project_header(pj)
        st.write("")
        {"explore": tab_explore, "path": tab_path, "approval": tab_approval}[ss.tab if (ss.tab != "approval" or pj["stage"] == "approval") else "path"](pj)
        sidebar_chat(pj)
        h = hash_of(pj)
        if h != ss.pj_hash:
            db.save_project(ss.user["id"], ss.pid, pj)
            ss.pj_hash = h
    footer()
