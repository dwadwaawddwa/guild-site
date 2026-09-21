"""Site guilde Albion — application FastAPI."""
import datetime as dt
import os
import secrets

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, config, discord_api
from .db import (Build, Call, CallSignup, Composition, CompSlot, SessionLocal,
                User, get_settings, init_db, now)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUILD_CATEGORIES = [
    ("tank", "Tank"),
    ("dps_melee", "DPS mêlée"),
    ("dps_range", "DPS distance"),
    ("support", "Support"),
    ("heal", "Heal"),
]
BUILD_CAT_LABEL = dict(BUILD_CATEGORIES)


def group_comp_slots(comp):
    """Regroupe les slots d'une compo par categorie (ordre fixe) + 'Autres roles'."""
    by_cat = {k: [] for k, _ in BUILD_CATEGORIES}
    other = []
    for slot in comp.slots:
        (by_cat[slot.category] if slot.category in by_cat else other).append(slot)
    groups = [(label, by_cat[key]) for key, label in BUILD_CATEGORIES if by_cat[key]]
    if other:
        groups.append(("Autres rôles", other))
    return groups


app = FastAPI(title="Guilde Albion")
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET,
                  https_only=config.COOKIE_SECURE, same_site="lax")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

init_db()


# ---------- helpers ----------
def db_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def current_user(request: Request) -> dict | None:
    return request.session.get("user")


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_hex(16)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, token: str):
    if not token or token != request.session.get("csrf"):
        raise HTTPException(status_code=400, detail="CSRF invalide")


def render(request: Request, name: str, **ctx):
    user = current_user(request)
    base = {
        "request": request,
        "user": user,
        "is_member": auth.has_level(user, "member"),
        "is_caller": auth.has_level(user, "caller"),
        "is_admin": auth.has_level(user, "admin"),
        "configured": config.is_configured(),
        "csrf": csrf_token(request),
        "build_categories": BUILD_CATEGORIES,
        "build_cat_label": BUILD_CAT_LABEL,
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def require(request: Request, level: str):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/auth/login"})
    if not auth.has_level(user, level):
        raise HTTPException(status_code=403, detail="Acces refuse")
    return user


# ---------- pages publiques / auth ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return render(request, "index.html")


@app.get("/auth/login")
def login(request: Request):
    if not config.is_configured():
        return render(request, "not_configured.html")
    state = secrets.token_hex(16)
    request.session["oauth_state"] = state
    return RedirectResponse(discord_api.authorize_url(state), status_code=303)


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = ""):
    if not code or state != request.session.get("oauth_state"):
        raise HTTPException(status_code=400, detail="Etat OAuth invalide")
    token = await discord_api.exchange_code(code)
    du = await discord_api.get_current_user(token["access_token"])
    with SessionLocal() as s:
        synced = await auth.sync_user_roles(s, du)
        u = s.get(User, str(du["id"])) or User(discord_id=str(du["id"]))
        u.username = du.get("username", "")
        u.global_name = du.get("global_name") or du.get("username", "")
        u.avatar = du.get("avatar") or ""
        u.role_ids = synced["role_ids"]
        u.access_level = synced["access_level"]
        u.last_login = now()
        s.add(u)
        s.commit()
        request.session["user"] = {
            "id": u.discord_id, "username": u.username, "name": u.global_name,
            "avatar": u.avatar, "access_level": u.access_level,
        }
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- dashboard (membre) ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        calls = (s.query(Call).filter(Call.status == "open")
                .order_by(Call.scheduled_at.is_(None), Call.scheduled_at).limit(10).all())
        comps = s.query(Composition).order_by(Composition.created_at.desc()).limit(6).all()
        return render(request, "dashboard.html", calls=calls, comps=comps)


# ---------- calls ----------
@app.get("/calls", response_class=HTMLResponse)
def calls_list(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        calls = s.query(Call).order_by(Call.created_at.desc()).all()
        return render(request, "calls.html", calls=calls)


@app.get("/calls/new", response_class=HTMLResponse)
def call_new_form(request: Request):
    require(request, "caller")
    with SessionLocal() as s:
        comps = s.query(Composition).order_by(Composition.name).all()
        return render(request, "call_new.html", comps=comps)


@app.post("/calls/new")
def call_new(request: Request, csrf: str = Form(""), title: str = Form(...),
            type: str = Form("small_scale"), description: str = Form(""),
            scheduled_at: str = Form(""), composition_id: str = Form("")):
    require(request, "caller")
    check_csrf(request, csrf)
    user = current_user(request)
    sched = None
    if scheduled_at:
        try:
            sched = dt.datetime.fromisoformat(scheduled_at)
        except ValueError:
            sched = None
    with SessionLocal() as s:
        c = Call(title=title.strip(), type=type, description=description.strip(),
                scheduled_at=sched, created_by=user["id"],
                composition_id=int(composition_id) if composition_id else None)
        s.add(c)
        s.commit()
        return RedirectResponse(f"/calls/{c.id}", status_code=303)


def _slot_label(slot, pos):
    if slot.role_name and slot.build_name:
        return f"{slot.role_name} — {slot.build_name}"
    return slot.role_name or slot.build_name or f"Slot {pos + 1}"


def call_options(session, call):
    """Options selectionnables d'un call : slots de la compo (doublons inclus),
    sinon la bibliotheque de builds. Chaque option a une reference unique."""
    opts = []
    if call.composition_id:
        comp = session.get(Composition, call.composition_id)
        if comp:
            for pos, sl in enumerate(comp.slots):
                opts.append({"ref": f"s{pos}", "label": _slot_label(sl, pos),
                            "weapon": sl.weapon, "offhand": sl.offhand, "head": sl.head,
                            "chest": sl.chest, "shoes": sl.shoes})
            return opts
    for b in session.query(Build).order_by(Build.name).all():
        opts.append({"ref": f"b{b.id}", "label": b.name, "weapon": b.weapon,
                    "offhand": b.offhand, "head": b.head, "chest": b.chest, "shoes": b.shoes})
    return opts


def resolve_ref(session, call, ref):
    """Retourne {id(ref), name} pour une reference d'option choisie."""
    if ref.startswith("s") and call.composition_id:
        try:
            pos = int(ref[1:])
        except ValueError:
            return None
        comp = session.get(Composition, call.composition_id)
        if comp and 0 <= pos < len(comp.slots):
            return {"id": ref, "name": _slot_label(comp.slots[pos], pos)}
    if ref.startswith("b"):
        try:
            b = session.get(Build, int(ref[1:]))
        except ValueError:
            return None
        if b:
            return {"id": ref, "name": b.name}
    return None


@app.get("/calls/{call_id}", response_class=HTMLResponse)
def call_detail(request: Request, call_id: int):
    require(request, "member")
    user = current_user(request)
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        if not c:
            raise HTTPException(404)
        options = call_options(s, c)
        # demande par build (premier arrive premier servi) : build -> [(prio, user, status)]
        demand = {}
        my_choice_refs = []
        for su in c.signups:
            choices = su.build_choices or []
            if su.user_id == user["id"]:
                my_choice_refs = [ch["id"] for ch in choices]
            for i, ch in enumerate(choices):
                demand.setdefault(ch["name"], []).append(
                    {"prio": i + 1, "user": su.username, "status": su.status,
                     "at": su.created_at})
        # tri : priorite puis ordre d'inscription (premier arrive premier servi)
        for name in demand:
            demand[name].sort(key=lambda x: (x["prio"], x["at"] or now()))
        comp_groups = group_comp_slots(c.composition) if c.composition else []
        return render(request, "call_detail.html", call=c, options=options,
                     demand=demand, my_choice_refs=my_choice_refs, comp_groups=comp_groups)


@app.post("/calls/{call_id}/signup")
def call_signup(request: Request, call_id: int, csrf: str = Form(""),
                build_ids: str = Form(""), status: str = Form("in")):
    user = require(request, "member")
    check_csrf(request, csrf)
    refs = [x.strip() for x in build_ids.split(",") if x.strip()]
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        if not c:
            raise HTTPException(404)
        choices = []
        for ref in refs:
            r = resolve_ref(s, c, ref)
            if r:
                choices.append(r)
        first = choices[0] if choices else None
        existing = (s.query(CallSignup)
                    .filter(CallSignup.call_id == call_id, CallSignup.user_id == user["id"]).first())
        if existing:
            existing.build_choices = choices
            existing.build_id = None
            existing.build_name = first["name"] if first else ""
            existing.status = status
        else:
            s.add(CallSignup(call_id=call_id, user_id=user["id"], username=user["name"],
                            build_choices=choices, build_id=None,
                            build_name=first["name"] if first else "", status=status))
        s.commit()
    return RedirectResponse(f"/calls/{call_id}", status_code=303)


@app.post("/calls/{call_id}/close")
def call_close(request: Request, call_id: int, csrf: str = Form("")):
    require(request, "caller")
    check_csrf(request, csrf)
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        if c:
            c.status = "closed"
            s.commit()
    return RedirectResponse(f"/calls/{call_id}", status_code=303)


# ---------- builds ----------
def _apply_build_form(b: Build, form):
    b.name = form.get("name", "").strip()
    b.category = form.get("category", "")
    b.weapon = form.get("weapon", ""); b.offhand = form.get("offhand", "")
    b.head = form.get("head", ""); b.chest = form.get("chest", "")
    b.shoes = form.get("shoes", ""); b.cape = form.get("cape", "")
    b.food = form.get("food", ""); b.potion = form.get("potion", "")
    b.spells = {
        "q": form.get("spell_q", ""), "w": form.get("spell_w", ""),
        "e": form.get("spell_e", ""), "passive": form.get("spell_passive", ""),
        "head": form.get("spell_head", ""), "chest": form.get("spell_chest", ""),
        "shoes": form.get("spell_shoes", ""),
    }
    b.notes = form.get("notes", "")


@app.get("/builds", response_class=HTMLResponse)
def builds_list(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        builds = s.query(Build).order_by(Build.name).all()
        groups = {key: [] for key, _ in BUILD_CATEGORIES}
        groups["_none"] = []
        for b in builds:
            groups.get(b.category, groups["_none"]).append(b)
        return render(request, "builds.html", groups=groups)


@app.get("/builds/new", response_class=HTMLResponse)
def build_new_form(request: Request):
    require(request, "caller")
    return render(request, "build_form.html", build=None)


@app.post("/builds/new")
async def build_new(request: Request):
    require(request, "caller")
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    user = current_user(request)
    with SessionLocal() as s:
        b = Build(created_by=user["id"])
        _apply_build_form(b, form)
        s.add(b)
        s.commit()
        return RedirectResponse(f"/builds/{b.id}", status_code=303)


@app.get("/builds/{build_id}", response_class=HTMLResponse)
def build_detail(request: Request, build_id: int):
    require(request, "member")
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b:
            raise HTTPException(404)
        return render(request, "build_detail.html", build=b)


@app.get("/builds/{build_id}/edit", response_class=HTMLResponse)
def build_edit_form(request: Request, build_id: int):
    require(request, "caller")
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b:
            raise HTTPException(404)
        return render(request, "build_form.html", build=b)


@app.post("/builds/{build_id}/edit")
async def build_edit(request: Request, build_id: int):
    require(request, "caller")
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b:
            raise HTTPException(404)
        _apply_build_form(b, form)
        s.commit()
        return RedirectResponse(f"/builds/{b.id}", status_code=303)


# ---------- compositions ----------
@app.get("/compositions", response_class=HTMLResponse)
def comps_list(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        comps = s.query(Composition).order_by(Composition.created_at.desc()).all()
        return render(request, "compositions.html", comps=comps)


@app.get("/compositions/new", response_class=HTMLResponse)
def comp_new_form(request: Request):
    require(request, "caller")
    return render(request, "comp_edit.html", comp=None)


@app.get("/compositions/{comp_id}", response_class=HTMLResponse)
def comp_view(request: Request, comp_id: int):
    require(request, "member")
    with SessionLocal() as s:
        comp = s.get(Composition, comp_id)
        if not comp:
            raise HTTPException(404)
        if auth.has_level(current_user(request), "caller"):
            return render(request, "comp_edit.html", comp=comp)
        return render(request, "comp_view.html", comp=comp, groups=group_comp_slots(comp))


@app.post("/compositions/save")
async def comp_save(request: Request):
    require(request, "caller")
    payload = await request.json()
    check_csrf(request, payload.get("csrf", ""))
    user = current_user(request)
    with SessionLocal() as s:
        if payload.get("id"):
            comp = s.get(Composition, int(payload["id"]))
            if not comp:
                raise HTTPException(404)
        else:
            comp = Composition(created_by=user["id"])
            s.add(comp)
        comp.name = (payload.get("name") or "Compo sans nom").strip()
        comp.content_type = payload.get("content_type", "small_scale")
        comp.notes = payload.get("notes", "")
        # remplace les slots
        comp.slots.clear()
        s.flush()
        for i, slot in enumerate(payload.get("slots", [])):
            comp.slots.append(CompSlot(
                position=i,
                role_name=slot.get("role_name", ""),
                category=slot.get("category", ""),
                build_name=slot.get("build_name", ""),
                weapon=slot.get("weapon", ""), offhand=slot.get("offhand", ""),
                head=slot.get("head", ""), chest=slot.get("chest", ""),
                shoes=slot.get("shoes", ""),
                assigned_user_id=str(slot.get("assigned_user_id", "")),
                assigned_username=slot.get("assigned_username", ""),
            ))
        s.commit()
        return JSONResponse({"ok": True, "id": comp.id})


# ---------- admin ----------
@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_form(request: Request):
    require(request, "admin")
    with SessionLocal() as s:
        st = get_settings(s)
        roles = await discord_api.get_guild_roles(st.guild_id) if st.guild_id else []
        return render(request, "admin_settings.html", settings=st, roles=roles)


@app.post("/admin/settings")
async def admin_settings_save(request: Request):
    require(request, "admin")
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    with SessionLocal() as s:
        st = get_settings(s)
        st.guild_id = form.get("guild_id", "").strip()
        st.member_role_ids = form.getlist("member_roles")
        st.caller_role_ids = form.getlist("caller_roles")
        st.admin_role_ids = form.getlist("admin_roles")
        s.commit()
    return RedirectResponse("/admin/settings", status_code=303)


# ---------- API interne ----------
@app.get("/api/members")
async def api_members(request: Request):
    require(request, "caller")
    with SessionLocal() as s:
        st = get_settings(s)
    members = await discord_api.list_members(st.guild_id) if st.guild_id else []
    out = []
    for m in members:
        u = m.get("user", {})
        if u.get("bot"):
            continue
        out.append({"id": u.get("id"), "name": m.get("nick") or u.get("global_name") or u.get("username")})
    out.sort(key=lambda x: (x["name"] or "").lower())
    return JSONResponse(out)


@app.get("/healthz")
def healthz():
    return {"ok": True, "configured": config.is_configured()}
