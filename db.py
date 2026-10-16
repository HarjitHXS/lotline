"""Accounts, projects and uploaded files, stored in SQLite + a folder on this server.

Demo-grade auth: scrypt-hashed passwords, per-process lockout after repeated failures, every query scoped to the
signed-in user. Data lives on the machine running the app (on a free cloud host it can be wiped on restart).
"""
import hashlib
import hmac
import json
import re
import secrets
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent / "data" / "user"
DB_PATH = ROOT / "app.db"
UPLOADS = ROOT / "uploads"
MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".csv", ".dwg", ".dxf"}
_FAILS: dict[str, list[float]] = {}  # email -> timestamps of recent failed sign-ins (lives as long as the server process)


def _conn() -> sqlite3.Connection:
    ROOT.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS sessions(
        token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS projects(
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, address TEXT NOT NULL, stage TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS uploads(
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        item_key TEXT NOT NULL, filename TEXT NOT NULL, stored TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL);
    """)
    return c


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


# ---------------------------------------------------------------- users
def create_user(email: str, name: str, password: str) -> tuple[dict | None, str]:
    email, name = email.strip().lower(), name.strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return None, "Enter a valid email address."
    if not name:
        return None, "Enter your name."
    if len(password) < 8:
        return None, "Use a password of at least 8 characters."
    salt = secrets.token_bytes(16)
    try:
        with _conn() as c:
            cur = c.execute("INSERT INTO users(email,name,salt,pw_hash,created) VALUES(?,?,?,?,?)",
                            (email, name, salt, _hash(password, salt), time.time()))
            return {"id": cur.lastrowid, "email": email, "name": name}, ""
    except sqlite3.IntegrityError:
        return None, "An account with that email already exists. Sign in instead."


def authenticate(email: str, password: str) -> tuple[dict | None, str]:
    email = email.strip().lower()
    recent = [t for t in _FAILS.get(email, []) if time.time() - t < 300]
    if len(recent) >= 5:
        return None, "Too many attempts. Wait 5 minutes and try again."
    with _conn() as c:
        row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = bool(row) and hmac.compare_digest(_hash(password, row["salt"]), row["pw_hash"])
    if not ok:
        _FAILS[email] = recent + [time.time()]
        return None, "Email or password is wrong."
    _FAILS.pop(email, None)
    return {"id": row["id"], "email": row["email"], "name": row["name"]}, ""


SESSION_HOURS = 8


def create_session(uid: int) -> str:
    """A random token kept in the page URL so a browser refresh doesn't sign you out. Sign-out revokes it."""
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)", (token, uid, time.time() + SESSION_HOURS * 3600))
    return token


def user_for_session(token: str) -> dict | None:
    if not token:
        return None
    with _conn() as c:
        r = c.execute("SELECT u.id,u.email,u.name FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
                      (token, time.time())).fetchone()
    return {"id": r["id"], "email": r["email"], "name": r["name"]} if r else None


def delete_session(token: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))


def create_demo_user() -> dict:
    """A throwaway account so a visitor can try the app without signing up."""
    tag = secrets.token_hex(3)
    user, _ = create_user(f"demo-{tag}@demo.local", "Demo builder", secrets.token_urlsafe(16))
    return user


# ---------------------------------------------------------------- projects
def list_projects(uid: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT id,name,address,stage,data,updated FROM projects WHERE user_id=? ORDER BY updated DESC", (uid,)).fetchall()
    return [{"id": r["id"], "name": r["name"], "address": r["address"], "stage": r["stage"], "updated": r["updated"],
             "data": json.loads(r["data"])} for r in rows]


def create_project(uid: int, data: dict) -> int:
    with _conn() as c:
        cur = c.execute("INSERT INTO projects(user_id,name,address,stage,data,updated) VALUES(?,?,?,?,?,?)",
                        (uid, data["name"], data["address"], data.get("stage", "explore"), json.dumps(data, default=str), time.time()))
        return cur.lastrowid


def get_project(uid: int, pid: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT data FROM projects WHERE id=? AND user_id=?", (pid, uid)).fetchone()
    return json.loads(r["data"]) if r else None


def save_project(uid: int, pid: int, data: dict) -> None:
    with _conn() as c:
        c.execute("UPDATE projects SET name=?, address=?, stage=?, data=?, updated=? WHERE id=? AND user_id=?",
                  (data["name"], data["address"], data.get("stage", "explore"), json.dumps(data, default=str), time.time(), pid, uid))


def delete_project(uid: int, pid: int) -> None:
    with _conn() as c:
        if c.execute("SELECT 1 FROM projects WHERE id=? AND user_id=?", (pid, uid)).fetchone():
            c.execute("DELETE FROM projects WHERE id=?", (pid,))
    shutil.rmtree(UPLOADS / str(pid), ignore_errors=True)


# ---------------------------------------------------------------- uploads
def _owns(c: sqlite3.Connection, uid: int, pid: int) -> bool:
    return bool(c.execute("SELECT 1 FROM projects WHERE id=? AND user_id=?", (pid, uid)).fetchone())


def add_upload(uid: int, pid: int, item_key: str, filename: str, data: bytes) -> tuple[bool, str]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return False, f"{filename}: file type {ext or '(none)'} isn't allowed. Use PDF, images, Word, Excel, text or CAD files."
    if len(data) > MAX_UPLOAD:
        return False, f"{filename}: larger than {MAX_UPLOAD // 1024 // 1024} MB."
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(filename).name)[:120] or "file"
    stored = f"{uuid.uuid4().hex}{ext}"
    with _conn() as c:
        if not _owns(c, uid, pid):
            return False, "Project not found."
        folder = UPLOADS / str(pid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / stored).write_bytes(data)
        c.execute("INSERT INTO uploads(project_id,item_key,filename,stored,size,created) VALUES(?,?,?,?,?,?)",
                  (pid, item_key, safe, stored, len(data), time.time()))
    return True, safe


def list_uploads(uid: int, pid: int) -> dict[str, list[dict]]:
    """All files for a project, grouped by checklist item key."""
    with _conn() as c:
        if not _owns(c, uid, pid):
            return {}
        rows = c.execute("SELECT id,item_key,filename,size,created FROM uploads WHERE project_id=? ORDER BY created", (pid,)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["item_key"], []).append({"id": r["id"], "filename": r["filename"], "size": r["size"], "created": r["created"]})
    return out


def read_upload(uid: int, pid: int, upload_id: int) -> bytes | None:
    with _conn() as c:
        if not _owns(c, uid, pid):
            return None
        r = c.execute("SELECT stored FROM uploads WHERE id=? AND project_id=?", (upload_id, pid)).fetchone()
    p = UPLOADS / str(pid) / r["stored"] if r else None
    return p.read_bytes() if p and p.exists() else None


def delete_upload(uid: int, pid: int, upload_id: int) -> None:
    with _conn() as c:
        if not _owns(c, uid, pid):
            return
        r = c.execute("SELECT stored FROM uploads WHERE id=? AND project_id=?", (upload_id, pid)).fetchone()
        if r:
            (UPLOADS / str(pid) / r["stored"]).unlink(missing_ok=True)
            c.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
