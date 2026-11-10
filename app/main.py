"""웹 서버 (FastAPI + Jinja2 + HTMX). 실행: uvicorn app.main:app --reload"""
from __future__ import annotations

import json
import sys
from urllib.parse import quote
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Form, Request  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402
from sqlalchemy import select  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from analysis.risk import RISK_HELP  # noqa: E402
from analysis.scoring import LABELS, METHODS, WEIGHTS  # noqa: E402
from analysis.themes import TREND_RULES  # noqa: E402
from app import filters, queries  # noqa: E402
from config import DISCLAIMER, FEATURES, can_use, is_pro_feature, settings  # noqa: E402
from db.models import Feedback, JobRun, UsageEvent, User  # noqa: E402
from db.session import init_db, session_scope  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
settings.check_production()


@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")
filters.register(templates.env)
templates.env.globals.update(DISCLAIMER=DISCLAIMER, APP_NAME=settings.app_name, is_pro=is_pro_feature)

PUBLIC = ("/login", "/static", "/manifest.webmanifest", "/sw.js", "/healthz", "/offline")
FEATURE_BY_PATH = {"/": "F01_dashboard", "/search": "F02_search", "/stock": "F07_stock_detail",
                   "/theme": "F03_ranking", "/about": None}


def current_user(request: Request) -> dict | None:
    return request.session.get("user")


def open_mode() -> bool:
    """초대 코드가 설정되지 않으면 로그인 없이 사용 (로컬 개발용)."""
    return not settings.invite_codes


@app.middleware("http")
async def gate_and_log(request: Request, call_next):
    path = request.url.path
    if not path.startswith(PUBLIC) and not open_mode() and not request.session.get("user"):
        if request.headers.get("HX-Request"):
            return HTMLResponse("", headers={"HX-Redirect": "/login"})
        return RedirectResponse(f"/login?next={path}", status_code=303)
    response = await call_next(request)
    if request.method == "GET" and not path.startswith(PUBLIC) and response.status_code == 200 \
            and not request.headers.get("HX-Request"):
        prefix = "/" + path.strip("/").split("/")[0] if path != "/" else "/"
        user = request.session.get("user") or {}
        with session_scope() as s:
            s.add(UsageEvent(user_id=user.get("id"), event="page_view", feature_id=FEATURE_BY_PATH.get(prefix),
                             path=str(request.url.path)[:300]))
    return response


def ctx(request: Request, **kw) -> dict:
    user = current_user(request) or ({"email": "개발 모드", "tier": "beta"} if open_mode() else None)
    tier = (user or {}).get("tier", "free")
    with session_scope() as s:
        demo = queries.is_demo(s)
    return dict(request=request, user=user, tier=tier, demo=demo, open_mode=open_mode(),
                can=lambda f: can_use(tier, f), **kw)


# ───────────── 페이지 ─────────────
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with session_scope() as s:
        data = queries.dashboard(s)
    return templates.TemplateResponse(request, "dashboard.html", ctx(request, **data, page="home"))


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", sort: str = "score"):
    data = None
    if q.strip():
        with session_scope() as s:
            data = queries.search(s, q.strip(), sort)
    tpl = "partials/stock_table.html" if request.headers.get("HX-Request") and data else "search.html"
    return templates.TemplateResponse(request, tpl, ctx(request, q=q, data=data, sorts=queries.SORTS,
                                                        page="search", sort_base=f"/search?q={quote(q)}"))


@app.get("/theme/{theme_id}", response_class=HTMLResponse)
def theme(request: Request, theme_id: str, sort: str = "score"):
    with session_scope() as s:
        data = queries.theme_detail(s, theme_id, sort)
    if not data:
        return HTMLResponse("테마를 찾을 수 없습니다.", status_code=404)
    tpl = "partials/stock_table.html" if request.headers.get("HX-Request") else "theme.html"
    return templates.TemplateResponse(request, tpl, ctx(request, data=data, sorts=queries.SORTS, page="theme",
                                                        sort_base=f"/theme/{theme_id}"))


@app.get("/stock/{code}", response_class=HTMLResponse)
def stock(request: Request, code: str):
    with session_scope() as s:
        data = queries.stock_detail(s, code)
    if not data:
        return HTMLResponse("종목을 찾을 수 없습니다.", status_code=404)
    chart = [dict(time=p.day.isoformat(), open=p.open or p.close, high=p.high or p.close, low=p.low or p.close,
                  close=p.close, volume=p.volume or 0) for p in data["prices"]]
    return templates.TemplateResponse(request, "stock.html", ctx(
        request, d=data, chart_json=json.dumps(chart), labels=LABELS, weights=WEIGHTS, methods=METHODS,
        risk_help=RISK_HELP, page="stock"))


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    with session_scope() as s:
        runs = list(s.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(15)))
    return templates.TemplateResponse(request, "about.html", ctx(
        request, labels=LABELS, weights=WEIGHTS, methods=METHODS, trend_rules=TREND_RULES, risk_help=RISK_HELP,
        features=FEATURES, runs=runs, page="about"))


# ───────────── 로그인 (초대 코드) ─────────────
@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", ctx(request, next=next, error=None, page="login"))


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(...), code: str = Form(...), next: str = Form("/")):
    email = email.strip().lower()
    if code.strip() not in settings.invite_codes or "@" not in email:
        return templates.TemplateResponse(request, "login.html", ctx(
            request, next=next, error="이메일 또는 초대 코드를 확인해 주세요.", page="login"), status_code=400)
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email))
        if not user:
            user = User(email=email, tier="beta")
            s.add(user)
            s.flush()
        request.session["user"] = {"id": user.id, "email": user.email, "tier": user.tier}
        s.add(UsageEvent(user_id=user.id, event="login"))
    return RedirectResponse(next if next.startswith("/") else "/", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ───────────── 피드백·이벤트 ─────────────
@app.post("/feedback", response_class=HTMLResponse)
def feedback(request: Request, page: str = Form(""), rating: int | None = Form(None), comment: str = Form("")):
    if not comment.strip() and rating is None:
        return HTMLResponse('<p class="text-sm text-rose-600">평점이나 의견을 입력해 주세요.</p>')
    user = current_user(request) or {}
    with session_scope() as s:
        s.add(Feedback(user_id=user.get("id"), page=page[:300], rating=rating, comment=comment.strip()[:4000]))
    return HTMLResponse('<p class="text-sm text-emerald-700">의견 감사합니다. 다음 업데이트에 반영하겠습니다.</p>')


@app.post("/api/event")
async def event(request: Request):
    body = await request.json()
    user = current_user(request) or {}
    with session_scope() as s:
        s.add(UsageEvent(user_id=user.get("id"), event=str(body.get("event", ""))[:40],
                         feature_id=str(body.get("feature", ""))[:40] or None, path=str(body.get("path", ""))[:300]))
    return JSONResponse({"ok": True})


# ───────────── PWA ─────────────
@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(APP_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(APP_DIR / "static" / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@app.get("/offline", response_class=HTMLResponse)
def offline(request: Request):
    return templates.TemplateResponse(request, "offline.html", {"request": request})


# 세션 미들웨어는 마지막에 추가해야 가장 바깥에서 실행되어 gate_and_log가 세션을 읽을 수 있다.
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=60 * 60 * 24 * 30, same_site="lax",
                   https_only=settings.is_production)


@app.get("/healthz")
def healthz():
    return {"ok": True}
