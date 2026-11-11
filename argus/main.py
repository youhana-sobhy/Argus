"""FastAPI application: JSON API + server-rendered web UI."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from argus import __version__
from argus.api.routes import router, system_info
from argus.config import PACKAGE_DIR, get_settings
from argus.core.security import Principal, current_user_optional, seed_users
from argus.db import init_db, session

WEB = PACKAGE_DIR / "web"
templates = Jinja2Templates(directory=str(WEB / "templates"))

PAGES = {
    "overview": ("/", "Overview"),
    "topology": ("/topology", "Topology"),
    "inventory": ("/inventory", "Inventory"),
    "assistant": ("/assistant", "Assistant"),
    "changes": ("/changes", "Changes"),
    "audit": ("/audit", "Audit log"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with session() as db:
        seed_users(db, get_settings().demo_password)
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Argus", version=__version__, lifespan=lifespan,
                  description="AI-assisted network automation platform - Cisco ACI MVP")
    app.add_middleware(SessionMiddleware, secret_key=s.secret_key, session_cookie="argus_session",
                       same_site="strict", https_only=False, max_age=8 * 3600)
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
    app.include_router(router)

    def page(name: str, template: str, permission: str | None = None):
        async def view(request: Request, user: Principal | None = Depends(current_user_optional), change_id: int | None = None):
            if user is None:
                return RedirectResponse("/login", status_code=303)
            if permission and not user.can(permission):
                return RedirectResponse("/", status_code=303)
            return templates.TemplateResponse(request, template, {
                "user": user.as_dict(), "page": name, "pages": PAGES, "system": system_info(), "change_id": change_id,
            })
        return view

    app.get("/", response_class=HTMLResponse, include_in_schema=False)(page("overview", "overview.html"))
    app.get("/topology", response_class=HTMLResponse, include_in_schema=False)(page("topology", "topology.html"))
    app.get("/inventory", response_class=HTMLResponse, include_in_schema=False)(page("inventory", "inventory.html"))
    app.get("/assistant", response_class=HTMLResponse, include_in_schema=False)(page("assistant", "assistant.html"))
    app.get("/changes", response_class=HTMLResponse, include_in_schema=False)(page("changes", "changes.html"))
    app.get("/changes/new", response_class=HTMLResponse, include_in_schema=False)(page("changes", "change_new.html"))
    app.get("/changes/{change_id}", response_class=HTMLResponse, include_in_schema=False)(page("changes", "change_detail.html"))
    app.get("/audit", response_class=HTMLResponse, include_in_schema=False)(page("audit", "audit.html", "audit.read"))

    @app.get("/present", response_class=HTMLResponse, include_in_schema=False)
    async def present_page(request: Request):
        # Public executive overview for management demos. Static content only: no fabric data.
        return templates.TemplateResponse(request, "present.html", {"system": system_info()})

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    async def login_page(request: Request, user: Principal | None = Depends(current_user_optional)):
        if user is not None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"system": system_info()})

    return app


app = create_app()
