"""FastAPI-приложение: API + статический интерфейс."""
from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .config import Account, settings
from ingest.load import connect

app = FastAPI(title="amazon-ads-dashboard", docs_url="/api/docs", redoc_url=None)

STATIC = Path(__file__).with_name("static")
COOKIE = "jd_session"


# --- Кабинеты --------------------------------------------------------------

def current_account(account: str | None = Query(default=None, description="ID кабинета")) -> Account:
    accounts = settings.accounts()
    key = account or settings.default_account
    if key not in accounts:
        raise HTTPException(status_code=404, detail=f"Неизвестный кабинет: {key}")
    return accounts[key]


def db(acc: Account = Depends(current_account)):
    conn = connect(acc.db_path)
    try:
        yield conn
    finally:
        conn.close()


# --- Авторизация -----------------------------------------------------------

def _sign(value: str) -> str:
    return hmac.new(settings.secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _session_value() -> str:
    stamp = str(int(datetime.now(timezone.utc).timestamp() // 3600))
    return f"{stamp}.{_sign(stamp)}"


def _valid_session(token: str | None) -> bool:
    if not settings.password:
        return True
    if not token or "." not in token:
        return False
    stamp, signature = token.split(".", 1)
    if not hmac.compare_digest(signature, _sign(stamp)):
        return False
    try:
        age = datetime.now(timezone.utc).timestamp() // 3600 - int(stamp)
    except ValueError:
        return False
    return 0 <= age < 24 * 14


def require_auth(request: Request):
    if not _valid_session(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="auth required")


@app.middleware("http")
async def guard(request: Request, call_next):
    public = ("/login", "/api/login", "/api/health", "/static", "/favicon.ico")
    if settings.password and not any(request.url.path.startswith(p) for p in public):
        if not _valid_session(request.cookies.get(COOKIE)):
            if request.url.path.startswith("/api/"):
                return Response(status_code=401, content='{"detail":"auth required"}',
                                media_type="application/json")
            return RedirectResponse("/login", status_code=302)
    return await call_next(request)


class LoginBody(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginBody):
    if not settings.password:
        return {"ok": True}
    if not hmac.compare_digest(body.password, settings.password):
        raise HTTPException(status_code=403, detail="Неверный пароль")
    response = Response(content='{"ok":true}', media_type="application/json")
    response.set_cookie(COOKIE, _session_value(), max_age=14 * 24 * 3600,
                        httponly=True, samesite="lax", secure=settings.secure_cookie)
    return response


@app.post("/api/logout")
def logout():
    response = Response(content='{"ok":true}', media_type="application/json")
    response.delete_cookie(COOKIE)
    return response


# --- API -------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@app.get("/api/accounts")
def api_accounts():
    return {
        "default": settings.default_account,
        "accounts": [
            {"id": a.id, "label": a.label, "profile_id": a.profile_id,
             "currency": a.currency, "timezone": a.timezone}
            for a in settings.accounts().values()
        ],
    }


@app.get("/api/meta")
def api_meta(acc: Account = Depends(current_account), conn=Depends(db)):
    data = queries.meta(conn)
    data["account"] = {
        "id": acc.id,
        "name": acc.label,
        "profile_id": acc.profile_id,
        "currency": acc.currency,
        "timezone": acc.timezone,
        "data_through": data["date_max"],
    }
    data["accounts"] = [{"id": a.id, "label": a.label} for a in settings.accounts().values()]
    return data


def _window(start: str | None, end: str | None, days: int = 7) -> tuple[str, str]:
    today = date.today()
    end_d = date.fromisoformat(end) if end else today
    start_d = date.fromisoformat(start) if start else end_d - timedelta(days=days - 1)
    if start_d > end_d:
        raise HTTPException(status_code=400, detail="Начало периода позже конца")
    return start_d.isoformat(), end_d.isoformat()


def _campaigns(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [c.strip() for c in raw.split(",") if c.strip()]
    return items or None


@app.get("/api/overview")
def api_overview(start: str | None = None, end: str | None = None,
                 campaigns: str | None = None, grain: str = "day",
                 conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end)
    return queries.overview(conn, s, e, _campaigns(campaigns), grain)


@app.get("/api/campaigns")
def api_campaigns(start: str | None = None, end: str | None = None,
                  campaigns: str | None = None, conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end)
    return queries.campaigns(conn, s, e, _campaigns(campaigns))


@app.get("/api/targets")
def api_targets(start: str | None = None, end: str | None = None,
                campaigns: str | None = None, kind: str = "positive",
                q: str | None = None, conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end, 30)
    return queries.targets(conn, s, e, _campaigns(campaigns), kind, q)


@app.get("/api/terms")
def api_terms(start: str | None = None, end: str | None = None,
              campaigns: str | None = None, q: str | None = None,
              orders_only: bool = False, conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end, 30)
    return queries.search_terms(conn, s, e, _campaigns(campaigns), q, orders_only)


@app.get("/api/placements")
def api_placements(start: str | None = None, end: str | None = None,
                   campaigns: str | None = None, conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end, 30)
    return queries.placements(conn, s, e, _campaigns(campaigns))


@app.get("/api/products")
def api_products(start: str | None = None, end: str | None = None,
                 campaigns: str | None = None, conn=Depends(db), _=Depends(require_auth)):
    s, e = _window(start, end, 30)
    return queries.products(conn, s, e, _campaigns(campaigns))


@app.get("/api/changes")
def api_changes(start: str | None = None, end: str | None = None,
                campaigns: str | None = None, q: str | None = None,
                conn=Depends(db), _=Depends(require_auth)):
    today = date.today()
    s = start or (today - timedelta(days=90)).isoformat()
    e = end or today.isoformat()
    return queries.changes(conn, s, e, _campaigns(campaigns), q)


@app.get("/api/coverage")
def api_coverage(conn=Depends(db), _=Depends(require_auth)):
    return queries.coverage(conn)


# --- Интерфейс -------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page():
    return (STATIC / "login.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
