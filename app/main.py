"""Site guilde Albion — application FastAPI."""
import datetime as dt
import json
import os
import secrets
import time

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                              RedirectResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import auth, comp_image, config, discord_api, killboard
from .db import (Build, Call, CallPost, CallSignup, Composition, CompSlot, SessionLocal,
                User, Workspace, all_workspaces, get_settings, get_ws, init_db, now)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUILD_CATEGORIES = [
    ("tank", "Tank"),
    ("dps_melee", "DPS mêlée"),
    ("dps_range", "DPS distance"),
    ("support", "Support"),
    ("heal", "Heal"),
]
BUILD_CAT_LABEL = dict(BUILD_CATEGORIES)

# types de call/compo suggeres (l'utilisateur peut aussi taper le sien)
CALL_TYPES = ["Small scale", "ZvZ", "PvE", "Ganking", "Faction", "Hellgate",
              "Avalon", "Corrupted", "Arena"]


def parse_utc(iso: str):
    """ISO recu du navigateur (deja en UTC) -> datetime UTC naif pour la base."""
    if not iso:
        return None
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo:
        d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return d


def to_unix(d):
    """datetime UTC naif -> timestamp unix (pour l'affichage localise)."""
    if not d:
        return None
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp())


def utcnow_naive():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def discord_avatar(uid: str, avatar: str) -> str:
    """URL de l'avatar Discord d'un membre (ou avatar par defaut)."""
    if avatar:
        return f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=64"
    try:
        idx = (int(uid) >> 22) % 6
    except (ValueError, TypeError):
        idx = 0
    return f"https://cdn.discordapp.com/embed/avatars/{idx}.png"


def is_finished(call) -> bool:
    return bool(call.end_at) and call.end_at < utcnow_naive()


def build_to_dict(b):
    return {"id": b.id, "name": b.name, "category": b.category,
            "weapon": b.weapon, "offhand": b.offhand, "head": b.head, "chest": b.chest,
            "shoes": b.shoes, "cape": b.cape, "food": b.food, "potion": b.potion,
            "swaps": b.swaps or {}}


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


SITE_NAME = "DWAlbion builder"

# mode invite : contenu prive (workspace 0), quotas pour eviter le spam
GUEST_WS = 0
GUEST_MAX_BUILDS = 100
GUEST_MAX_COMPS = 50
GUEST_WRITES_PER_HOUR = 60  # creations par IP et par heure

app = FastAPI(title=SITE_NAME)
app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET,
                  https_only=config.COOKIE_SECURE, same_site="lax")
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

init_db()


# ---------- helpers ----------
_ITEM_IDX = None


def item_index():
    """nom FR (minuscule) -> {unique, name, name_en}. Le nom FR est la valeur stockee."""
    global _ITEM_IDX
    if _ITEM_IDX is None:
        with open(os.path.join(BASE_DIR, "static", "albion_items.json"), encoding="utf-8") as f:
            data = json.load(f)
        _ITEM_IDX = {it["name"].lower(): it for items in data.values() for it in items}
    return _ITEM_IDX


def translate_item(name: str, lang: str) -> str:
    """Traduit un nom d'item stocke (FR) vers la langue affichee."""
    if not name or lang != "en":
        return name
    it = item_index().get(name.lower())
    return (it.get("name_en") or name) if it else name


def get_lang(request: Request) -> str:
    return "en" if request.cookies.get("lang") == "en" else "fr"


def db_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def ws_access(request: Request) -> dict:
    return request.session.get("ws_access") or {}


def active_ws_id(request: Request) -> int:
    """ID du serveur actif (parmi ceux accessibles), defaut 1."""
    acc = ws_access(request)
    wid = str(request.session.get("ws") or "")
    if wid in acc:
        return int(wid)
    if acc:
        return int(sorted(acc, key=lambda x: int(x))[0])
    return 1


def current_level(request: Request) -> str:
    acc = ws_access(request)
    if acc:
        a = acc.get(str(active_ws_id(request)))
        return a["level"] if a else "guest"
    # session ouverte avant le multi-serveurs : conserve l'ancien niveau
    return (request.session.get("user") or {}).get("access_level", "guest")


def current_user(request: Request) -> dict | None:
    u = request.session.get("user")
    if not u:
        return None
    # le niveau d'acces depend du serveur actif
    return {**u, "access_level": current_level(request)}


def is_guest(user) -> bool:
    """Connecte sans role membre (invite, ou compte Discord sans role) : builder seulement
    (builds, compos, images) — pas de calls/activites/killboard/profil."""
    return bool(user) and not auth.has_level(user, "member")


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


_guest_writes: dict = {}  # ip -> [timestamps] des creations en mode invite


def check_guest_quota(request: Request, user, s, model, cap: int):
    """Anti-spam invite : plafond de creations par createur + N creations/heure par IP."""
    if not is_guest(user):
        return
    if s.query(model).filter(model.created_by == user["id"]).count() >= cap:
        raise HTTPException(status_code=429,
                            detail=f"Limite de {cap} atteinte en mode invité : supprime d'anciennes créations.")
    t = time.time()
    if len(_guest_writes) > 5000:  # purge des IP inactives
        for k in [k for k, v in _guest_writes.items() if not v or t - v[-1] > 3600]:
            del _guest_writes[k]
    ip = client_ip(request)
    hits = [x for x in _guest_writes.get(ip, []) if t - x < 3600]
    if len(hits) >= GUEST_WRITES_PER_HOUR:
        raise HTTPException(status_code=429, detail="Trop de créations, réessaie dans un moment.")
    hits.append(t)
    _guest_writes[ip] = hits


def _mine_private(model, user):
    """Contenu prive (mode invite) cree par l'utilisateur."""
    return (model.workspace_id == GUEST_WS) & (model.created_by == user["id"])


def visible_builds(s, request: Request, user):
    """Invite : ses builds seulement. Membre : ses serveurs + publics + ses builds prives."""
    if is_guest(user):
        return s.query(Build).filter(_mine_private(Build, user))
    return s.query(Build).filter(Build.workspace_id.in_(accessible_ws_ids(request))
                                 | (Build.public == True) | _mine_private(Build, user))


def visible_comps(s, request: Request, user):
    if is_guest(user):
        return s.query(Composition).filter(_mine_private(Composition, user))
    return s.query(Composition).filter(Composition.workspace_id.in_(accessible_ws_ids(request))
                                       | (Composition.public == True)
                                       | _mine_private(Composition, user))


def can_view(user, obj) -> bool:
    """Le contenu prive (invite) n'est visible que par son createur ; un invite ne voit que le sien."""
    if is_guest(user) or obj.workspace_id == GUEST_WS:
        return bool(user) and obj.created_by == user["id"]
    return True


def accessible_workspaces(request: Request):
    """[{id, name, active}] des serveurs accessibles a l'utilisateur (pour le switcher)."""
    acc = ws_access(request)
    aid = active_ws_id(request)
    return [{"id": int(k), "name": v.get("name") or f"Serveur {k}", "active": int(k) == aid}
            for k, v in sorted(acc.items(), key=lambda kv: int(kv[0]))]


def postable_workspaces(request: Request, need_caller: bool):
    """Serveurs ou l'utilisateur peut poster (caller pour calls, membre pour activites).

    has_channel = un salon Discord est configure -> le post est cree/coche automatiquement.
    """
    need = "caller" if need_caller else "member"
    aid = active_ws_id(request)
    ids = [int(k) for k, v in ws_access(request).items()
           if auth.LEVELS.get(v.get("level", "guest"), 0) >= auth.LEVELS[need]]
    out = []
    with SessionLocal() as s:
        for wid in ids:
            ws = get_ws(s, wid)
            if not ws:
                continue
            chan = ws.calls_channel_id if need_caller else ws.activities_channel_id
            out.append({"id": wid, "name": ws.name or f"Serveur {wid}",
                       "active": wid == aid, "has_channel": bool(chan)})
    return sorted(out, key=lambda x: x["id"])


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_hex(16)
        request.session["csrf"] = tok
    return tok


def check_csrf(request: Request, token: str):
    if not token or token != request.session.get("csrf"):
        raise HTTPException(status_code=400, detail="CSRF invalide")


def _asset_v() -> int:
    """Version des assets (mtime du CSS/JS) pour casser le cache Cloudflare au deploiement."""
    v = 1
    for f in ("style.css", "app.js"):
        try:
            v = max(v, int(os.path.getmtime(os.path.join(BASE_DIR, "static", f))))
        except OSError:
            pass
    return v


def render(request: Request, name: str, **ctx):
    user = current_user(request)
    lang = get_lang(request)
    base = {
        "request": request,
        "lang": lang,
        "t_item": lambda n: translate_item(n, lang),
        "user": user,
        "is_member": auth.has_level(user, "member"),
        "is_guest": is_guest(user),
        "site_name": SITE_NAME,
        "is_caller": auth.has_level(user, "caller"),
        "is_admin": auth.has_level(user, "admin"),
        "configured": config.is_configured(),
        "csrf": csrf_token(request),
        "build_categories": BUILD_CATEGORIES,
        "build_cat_label": BUILD_CAT_LABEL,
        "to_unix": to_unix,
        "is_finished": is_finished,
        "call_types": CALL_TYPES,
        "can_edit": lambda created_by: can_manage(user, created_by),
        "workspaces": accessible_workspaces(request),
        "asset_v": _asset_v(),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def require(request: Request, level: str):
    user = current_user(request)
    if not user:
        # pages du builder : accueil (choix Discord / invite) ; sinon direct Discord
        raise HTTPException(status_code=303,
                            headers={"Location": "/" if level == "guest" else "/auth/login"})
    if not auth.has_level(user, level):
        if is_guest(user):  # onglets reserves aux membres : retour a l'accueil du builder
            raise HTTPException(status_code=303, headers={"Location": "/dashboard"})
        raise HTTPException(status_code=403, detail="Acces refuse")
    return user


def require_builder(request: Request):
    """Builds, compos et images : membres ET invites."""
    return require(request, "guest")


def can_manage(user, created_by: str) -> bool:
    """Peut editer/supprimer : caller/admin, ou le createur de l'element."""
    if not user:
        return False
    return auth.has_level(user, "caller") or bool(created_by and created_by == user.get("id"))


def require_manage(request: Request, created_by: str):
    user = require_builder(request)
    if not can_manage(user, created_by):
        raise HTTPException(status_code=403, detail="Réservé au créateur ou aux callers")
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
    prev = request.session.get("user") or {}
    with SessionLocal() as s:
        access = await auth.sync_user_workspaces(s, du)
        u = s.get(User, str(du["id"])) or User(discord_id=str(du["id"]))
        u.username = du.get("username", "")
        u.global_name = du.get("global_name") or du.get("username", "")
        u.avatar = du.get("avatar") or ""
        u.last_login = now()
        s.add(u)
        if prev.get("guest"):  # invite qui se connecte : ses creations passent sur son compte
            for model in (Build, Composition):
                s.query(model).filter(model.created_by == prev["id"]).update(
                    {model.created_by: str(du["id"]), model.created_by_name: u.global_name})
        s.commit()
    request.session["user"] = {
        "id": str(du["id"]), "username": du.get("username", ""),
        "name": du.get("global_name") or du.get("username", ""),
        "avatar": du.get("avatar") or "",
    }
    request.session["ws_access"] = access
    request.session["ws"] = "1" if "1" in access else (next(iter(access)) if access else "1")
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/auth/guest")
def guest_login(request: Request, csrf: str = Form(""), name: str = Form("")):
    """Connexion invite (sans compte) : builds, compos et images uniquement."""
    check_csrf(request, csrf)
    prev = request.session.get("user") or {}
    if prev and not prev.get("guest"):  # deja connecte avec Discord
        return RedirectResponse("/dashboard", status_code=303)
    name = " ".join("".join(ch for ch in name if ch.isprintable()).split())[:24]
    request.session["user"] = {
        # garde l'id d'un invite deja connecte (sinon il perdrait ses creations)
        "id": prev.get("id") or "g" + secrets.token_hex(8),
        "name": name or prev.get("name") or f"Invité-{secrets.token_hex(2).upper()}",
        "avatar": "", "guest": True,
    }
    request.session["ws_access"] = {}
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/ws/{ws_id}")
def switch_ws(request: Request, ws_id: int):
    """Change le serveur actif (parmi ceux accessibles)."""
    if str(ws_id) in ws_access(request):
        request.session["ws"] = str(ws_id)
    return RedirectResponse(request.headers.get("referer") or "/dashboard", status_code=303)


@app.get("/lang/{code}")
def set_lang(request: Request, code: str):
    """Bascule FR/EN. Ne renvoie que vers un chemin interne (pas de redirection ouverte)."""
    from urllib.parse import urlparse
    lang = "en" if code == "en" else "fr"
    ref = request.headers.get("referer") or "/"
    p = urlparse(ref)
    target = p.path if p.path.startswith("/") else "/"
    if p.query:
        target += "?" + p.query
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("lang", lang, max_age=31536000, samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# ---------- dashboard (membre) ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = require_builder(request)
    if is_guest(user):  # accueil du builder : ses compos et builds
        with SessionLocal() as s:
            comps = (visible_comps(s, request, user)
                    .order_by(Composition.created_at.desc()).limit(6).all())
            builds = visible_builds(s, request, user).order_by(Build.created_at.desc()).limit(8).all()
            return render(request, "dashboard.html", calls=[], comps=comps, builds=builds)
    wsid = active_ws_id(request)
    with SessionLocal() as s:
        calls = (s.query(Call).filter(Call.status == "open", Call.workspace_id == wsid,
                                     Call.is_activity == False)
                .order_by(Call.scheduled_at.is_(None), Call.scheduled_at).all())
        calls = [c for c in calls if not is_finished(c)][:10]
        comps = (visible_comps(s, request, user)
                .order_by(Composition.created_at.desc()).limit(6).all())
        return render(request, "dashboard.html", calls=calls, comps=comps)


# ---------- calls ----------
def _list_calls(activity: bool, wsid: int):
    from sqlalchemy.orm import selectinload
    with SessionLocal() as s:
        # precharge les inscriptions : le template les lit apres fermeture de session
        calls = (s.query(Call).options(selectinload(Call.signups))
                .filter(Call.is_activity == activity, Call.workspace_id == wsid).all())
        far = dt.datetime.max
        calls.sort(key=lambda c: (is_finished(c), c.scheduled_at or far, -c.id))
        return calls


@app.get("/calls", response_class=HTMLResponse)
def calls_list(request: Request):
    require(request, "member")
    return render(request, "calls.html", calls=_list_calls(False, active_ws_id(request)),
                 is_activity_page=False, page_title="Calls")


@app.get("/activities", response_class=HTMLResponse)
def activities_list(request: Request):
    require(request, "member")
    return render(request, "calls.html", calls=_list_calls(True, active_ws_id(request)),
                 is_activity_page=True, page_title="Activités")


def accessible_ws_ids(request: Request) -> list:
    """Tous les serveurs Discord ou l'utilisateur est membre (compos/builds partages entre eux)."""
    ids = [int(k) for k in ws_access(request).keys()]
    return ids or [active_ws_id(request)]


def _visible_comps(s, ws_ids):
    return (s.query(Composition)
           .filter(Composition.workspace_id.in_(ws_ids) | (Composition.public == True))
           .order_by(Composition.name).all())


@app.get("/calls/new", response_class=HTMLResponse)
def call_new_form(request: Request):
    require(request, "caller")
    with SessionLocal() as s:
        comps = _visible_comps(s, accessible_ws_ids(request))
        return render(request, "call_new.html", comps=comps, is_activity=False,
                     post_workspaces=postable_workspaces(request, True))


@app.get("/activities/new", response_class=HTMLResponse)
def activity_new_form(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        comps = _visible_comps(s, accessible_ws_ids(request))
        return render(request, "call_new.html", comps=comps, is_activity=True,
                     post_workspaces=postable_workspaces(request, False))


def _create_call(request, activity, csrf, title, type, description,
                scheduled_at, end_at, composition_id, post_ws):
    user = require(request, "caller" if not activity else "member")
    check_csrf(request, csrf)
    wsid = active_ws_id(request)
    need = "caller" if not activity else "member"
    acc = ws_access(request)
    # serveurs cibles autorises (ou l'utilisateur a le niveau requis)
    targets = [int(w) for w in (post_ws or [])
               if str(w) in acc and auth.LEVELS.get(acc[str(w)]["level"], 0) >= auth.LEVELS[need]]
    with SessionLocal() as s:
        c = Call(title=title.strip(), type=type, description=description.strip(),
                scheduled_at=parse_utc(scheduled_at), end_at=parse_utc(end_at),
                created_by=user["id"], created_by_name=user["name"],
                is_activity=activity, needs_discord_post=False, workspace_id=wsid,
                composition_id=int(composition_id) if composition_id else None)
        s.add(c)
        s.commit()
        for tw in targets:  # un post par serveur cible
            s.add(CallPost(call_id=c.id, workspace_id=tw, needs_post=True))
        s.commit()
        cid = c.id
    return RedirectResponse(f"/calls/{cid}", status_code=303)


@app.post("/calls/new")
def call_new(request: Request, csrf: str = Form(""), title: str = Form(...),
            type: str = Form("small_scale"), description: str = Form(""),
            scheduled_at: str = Form(""), end_at: str = Form(""),
            composition_id: str = Form(""), post_ws: list[str] = Form(default=[])):
    return _create_call(request, False, csrf, title, type, description,
                       scheduled_at, end_at, composition_id, post_ws)


@app.post("/activities/new")
def activity_new(request: Request, csrf: str = Form(""), title: str = Form(...),
                type: str = Form("small_scale"), description: str = Form(""),
                scheduled_at: str = Form(""), end_at: str = Form(""),
                composition_id: str = Form(""), post_ws: list[str] = Form(default=[])):
    return _create_call(request, True, csrf, title, type, description,
                       scheduled_at, end_at, composition_id, post_ws)


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
                            "category": sl.category,
                            "weapon": sl.weapon, "offhand": sl.offhand, "head": sl.head,
                            "chest": sl.chest, "shoes": sl.shoes,
                            "cape": sl.cape, "food": sl.food, "potion": sl.potion,
                            "swaps": sl.swaps or {}})
            return opts
    for b in session.query(Build).order_by(Build.name).all():
        opts.append({"ref": f"b{b.id}", "label": b.name, "category": b.category,
                    "weapon": b.weapon, "offhand": b.offhand, "head": b.head,
                    "chest": b.chest, "shoes": b.shoes,
                    "cape": b.cape, "food": b.food, "potion": b.potion,
                    "swaps": b.swaps or {}})
    return opts


def group_call_options(options):
    """Groupe les options par categorie (ordre fixe) + 'Autres roles'."""
    by = {k: [] for k, _ in BUILD_CATEGORIES}
    other = []
    for o in options:
        (by[o["category"]] if o.get("category") in by else other).append(o)
    groups = [(label, by[key]) for key, label in BUILD_CATEGORIES if by[key]]
    if other:
        groups.append(("Autres rôles", other))
    return groups


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
        # avatars des membres inscrits
        uids = [su.user_id for su in c.signups]
        avatars = {}
        if uids:
            for u in s.query(User).filter(User.discord_id.in_(uids)).all():
                avatars[u.discord_id] = u.avatar

        demand = {}
        my_choice_refs = []
        picks_by_ref = {}  # ref -> [{name, av, prio, status}] (avatars par place)
        for su in c.signups:
            choices = su.build_choices or []
            if su.user_id == user["id"]:
                my_choice_refs = [ch["id"] for ch in choices]
            av = discord_avatar(su.user_id, avatars.get(su.user_id, ""))
            for i, ch in enumerate(choices):
                demand.setdefault(ch["name"], []).append(
                    {"prio": i + 1, "user": su.username, "status": su.status,
                     "at": su.created_at})
                picks_by_ref.setdefault(ch["id"], []).append(
                    {"name": su.username, "av": av, "prio": i + 1, "status": su.status})
        # tri : priorite puis ordre d'inscription (premier arrive premier servi)
        for name in demand:
            demand[name].sort(key=lambda x: (x["prio"], x["at"] or now()))

        # spec du joueur connecte pour chaque place (badge sur les cartes)
        me = s.get(User, user["id"])
        my_specs = (me.specs or {}) if me else {}
        for opt in options:
            opt["spec"] = weapon_spec(my_specs, opt["weapon"])

        # donnees du createur de roster (callers uniquement)
        roster = []
        if auth.has_level(user, "caller"):
            specs_by_user = {}
            for su in c.signups:
                uu = s.get(User, su.user_id)
                specs_by_user[su.user_id] = (uu.specs or {}) if uu else {}
            for opt in options:
                cands = []
                for su in c.signups:
                    refs = [ch["id"] for ch in (su.build_choices or [])]
                    if opt["ref"] in refs:
                        cands.append({
                            "user": su.username, "user_id": su.user_id,
                            "prio": refs.index(opt["ref"]) + 1,
                            "at": su.created_at.isoformat() if su.created_at else "",
                            "status": su.status,
                            "spec": weapon_spec(specs_by_user.get(su.user_id, {}), opt["weapon"]) or 0,
                        })
                roster.append({"ref": opt["ref"], "label": opt["label"],
                              "category": opt["category"], "weapon": opt["weapon"],
                              "candidates": cands})

        comp_groups = group_comp_slots(c.composition) if c.composition else []
        return render(request, "call_detail.html", call=c, options=options,
                     option_groups=group_call_options(options), roster=roster,
                     demand=demand, my_choice_refs=my_choice_refs, comp_groups=comp_groups,
                     picks_by_ref=picks_by_ref)


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


@app.post("/calls/{call_id}/roster")
def call_roster(request: Request, call_id: int, csrf: str = Form(""),
                roster: str = Form(""), action: str = Form("confirm")):
    """Le caller fige le roster (1 membre par slot) -> confirme et met a jour Discord."""
    require(request, "caller")
    check_csrf(request, csrf)
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        if not c:
            raise HTTPException(404)
        if action == "reset":  # rouvrir le roster (annuler le definitif)
            c.roster = []
            c.roster_final = False
            c.roster_synced = False
            s.commit()
            return RedirectResponse(f"/calls/{call_id}", status_code=303)
        try:
            chosen = json.loads(roster or "{}")  # {ref: user_id}
        except ValueError:
            chosen = {}
        labels = {o["ref"]: o["label"] for o in call_options(s, c)}
        names = {su.user_id: su.username for su in c.signups}
        final = [{"ref": ref, "label": labels.get(ref, ref),
                 "user_id": str(uid), "username": names.get(str(uid), "")}
                for ref, uid in chosen.items() if uid]
        c.roster = final
        c.roster_final = True
        c.roster_synced = False  # le bot mettra a jour les messages Discord
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


@app.post("/calls/{call_id}/delete")
def call_delete(request: Request, call_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        require_manage(request, c.created_by if c else "")
        if c:
            s.delete(c)  # cascade supprime les inscriptions
            s.commit()
    return RedirectResponse("/calls", status_code=303)


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
    b.swaps = {k: form.get(f"swap_{k}", "") for k in ("weapon", "offhand", "head", "chest", "shoes")
              if form.get(f"swap_{k}", "").strip()}
    b.public = bool(form.get("public"))
    b.notes = form.get("notes", "")


def _sync_build_into_slots(s, b: Build):
    """Recopie le build a jour dans tous les slots de compo qui l'utilisent.
    (les slots gardent une copie du build -> a resynchroniser quand le build change)."""
    for cs in s.query(CompSlot).filter(CompSlot.build_id == b.id).all():
        cs.build_name = b.name
        cs.weapon, cs.offhand = b.weapon, b.offhand
        cs.head, cs.chest, cs.shoes = b.head, b.chest, b.shoes
        cs.cape, cs.food, cs.potion = b.cape, b.food, b.potion
        cs.swaps = b.swaps or {}


@app.get("/builds", response_class=HTMLResponse)
def builds_list(request: Request):
    user = require_builder(request)
    with SessionLocal() as s:
        builds = visible_builds(s, request, user).order_by(Build.name).all()
        groups = {key: [] for key, _ in BUILD_CATEGORIES}
        groups["_none"] = []
        for b in builds:
            groups.get(b.category, groups["_none"]).append(b)
        return render(request, "builds.html", groups=groups)


@app.get("/builds/new", response_class=HTMLResponse)
def build_new_form(request: Request):
    require_builder(request)
    return render(request, "build_form.html", build=None)


@app.post("/builds/new")
async def build_new(request: Request):
    user = require_builder(request)
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    guest = is_guest(user)
    with SessionLocal() as s:
        check_guest_quota(request, user, s, Build, GUEST_MAX_BUILDS)
        b = Build(created_by=user["id"], created_by_name=user["name"],
                 official=auth.has_level(user, "caller"),
                 workspace_id=GUEST_WS if guest else active_ws_id(request))
        _apply_build_form(b, form)
        if guest:
            b.public = False
        s.add(b)
        s.commit()
        return RedirectResponse(f"/builds/{b.id}", status_code=303)


@app.post("/builds/{build_id}/delete")
def build_delete(request: Request, build_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        require_manage(request, b.created_by if b else "")
        if b:
            s.delete(b)
            s.commit()
    return RedirectResponse("/builds", status_code=303)


@app.get("/builds/{build_id}", response_class=HTMLResponse)
def build_detail(request: Request, build_id: int):
    user = require_builder(request)
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b or not can_view(user, b):
            raise HTTPException(404)
        return render(request, "build_detail.html", build=b)


@app.get("/builds/{build_id}/image.png")
def build_image_png(request: Request, build_id: int, download: int = 0):
    """Image PNG d'un build (meme style que les compos)."""
    user = require_builder(request)
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b or not can_view(user, b):
            raise HTTPException(404)
        sw = b.swaps or {}
        row = [(getattr(b, k), sw.get(k)) for k in comp_image.GEAR_ORDER if getattr(b, k)]
        cat = b.category if b.category in BUILD_CAT_LABEL else ""
        sections = [{"key": cat, "label": BUILD_CAT_LABEL.get(cat, "Build"), "rows": [row] if row else []}]
        name = b.name
    path = comp_image.render_comp(name, SITE_NAME, sections, f"build_{build_id}.png")
    return _png_response(path, name, download)


@app.get("/builds/{build_id}/edit", response_class=HTMLResponse)
def build_edit_form(request: Request, build_id: int):
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b:
            raise HTTPException(404)
        require_manage(request, b.created_by)
        return render(request, "build_form.html", build=b)


@app.post("/builds/{build_id}/edit")
async def build_edit(request: Request, build_id: int):
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    with SessionLocal() as s:
        b = s.get(Build, build_id)
        if not b:
            raise HTTPException(404)
        user = require_manage(request, b.created_by)
        _apply_build_form(b, form)
        if is_guest(user) or b.workspace_id == GUEST_WS:
            b.public = False  # le contenu invite reste prive
        _sync_build_into_slots(s, b)  # propage la modif dans les compos qui utilisent ce build
        s.commit()
        return RedirectResponse(f"/builds/{b.id}", status_code=303)


# ---------- compositions ----------
@app.get("/compositions", response_class=HTMLResponse)
def comps_list(request: Request):
    user = require_builder(request)
    with SessionLocal() as s:
        comps = visible_comps(s, request, user).order_by(Composition.created_at.desc()).all()
        return render(request, "compositions.html", comps=comps)


@app.get("/compositions/new", response_class=HTMLResponse)
def comp_new_form(request: Request):
    user = require_builder(request)
    with SessionLocal() as s:
        builds = [build_to_dict(b) for b in
                 visible_builds(s, request, user).order_by(Build.name).all()]
    return render(request, "comp_edit.html", comp=None, builds=builds)


@app.post("/compositions/{comp_id}/delete")
def comp_delete(request: Request, comp_id: int, csrf: str = Form("")):
    check_csrf(request, csrf)
    with SessionLocal() as s:
        comp = s.get(Composition, comp_id)
        require_manage(request, comp.created_by if comp else "")
        if comp:
            # detache les calls lies pour ne pas les supprimer
            for c in s.query(Call).filter(Call.composition_id == comp_id).all():
                c.composition_id = None
            s.delete(comp)  # cascade supprime les slots
            s.commit()
    return RedirectResponse("/compositions", status_code=303)


@app.get("/compositions/{comp_id}", response_class=HTMLResponse)
def comp_view(request: Request, comp_id: int):
    user = require_builder(request)
    with SessionLocal() as s:
        comp = s.get(Composition, comp_id)
        if not comp or not can_view(user, comp):
            raise HTTPException(404)
        if can_manage(user, comp.created_by):
            builds = [build_to_dict(b) for b in
                     visible_builds(s, request, user).order_by(Build.name).all()]
            return render(request, "comp_edit.html", comp=comp, builds=builds)
        return render(request, "comp_view.html", comp=comp, groups=group_comp_slots(comp))


def _png_response(path: str, name: str, download: int):
    """PNG genere a la volee : jamais en cache (Cloudflare met les .png en cache sinon)."""
    headers = {"Cache-Control": "private, no-store"}
    if download:
        from urllib.parse import quote
        name = (name or "image").strip()[:60] or "image"
        ascii_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "-_") else "_" for ch in name)
        headers["Content-Disposition"] = (f'attachment; filename="{ascii_name}.png"; '
                                          f"filename*=UTF-8''{quote(name + '.png')}")
    return FileResponse(path, media_type="image/png", headers=headers)


@app.get("/compositions/{comp_id}/image.png")
def comp_image_png(request: Request, comp_id: int, download: int = 0):
    """Image PNG de la composition (style albioncompo), aussi postee sur Discord."""
    user = require_builder(request)
    with SessionLocal() as s:
        comp = s.get(Composition, comp_id)
        if not comp or not can_view(user, comp):
            raise HTTPException(404)
        sections = comp_image.sections_from_comp(comp)
        name = comp.name
    path = comp_image.render_comp(name, comp_image.GUILD_NAME, sections, f"comp_{comp_id}.png")
    return _png_response(path, name, download)


@app.post("/compositions/save")
async def comp_save(request: Request):
    user = require_builder(request)
    payload = await request.json()
    check_csrf(request, payload.get("csrf", ""))
    guest = is_guest(user)
    with SessionLocal() as s:
        if payload.get("id"):
            comp = s.get(Composition, int(payload["id"]))
            if not comp:
                raise HTTPException(404)
            require_manage(request, comp.created_by)
        else:
            check_guest_quota(request, user, s, Composition, GUEST_MAX_COMPS)
            comp = Composition(created_by=user["id"], created_by_name=user["name"],
                              official=auth.has_level(user, "caller"),
                              workspace_id=GUEST_WS if guest else active_ws_id(request))
            s.add(comp)
        comp.name = (payload.get("name") or "Compo sans nom").strip()
        comp.content_type = payload.get("content_type", "small_scale")
        # le contenu invite reste prive
        comp.public = bool(payload.get("public")) and not guest and comp.workspace_id != GUEST_WS
        comp.notes = payload.get("notes", "")
        # remplace les slots
        comp.slots.clear()
        s.flush()
        for i, slot in enumerate(payload.get("slots", [])):
            cs = CompSlot(
                position=i,
                role_name=slot.get("role_name", ""),
                category=slot.get("category", ""),
                build_id=int(slot["build_id"]) if slot.get("build_id") else None,
                assigned_user_id="" if guest else str(slot.get("assigned_user_id", "")),
                assigned_username="" if guest else slot.get("assigned_username", ""),
            )
            # copie le build complet choisi (arme, armure, cape, food, potion)
            b = s.get(Build, cs.build_id) if cs.build_id else None
            if b and not can_view(user, b):  # un invite ne peut piocher que dans ses builds
                b, cs.build_id = None, None
            if b:
                cs.build_name = b.name
                cs.weapon, cs.offhand = b.weapon, b.offhand
                cs.head, cs.chest, cs.shoes = b.head, b.chest, b.shoes
                cs.cape, cs.food, cs.potion = b.cape, b.food, b.potion
                cs.swaps = b.swaps or {}
            comp.slots.append(cs)
        s.commit()
        return JSONResponse({"ok": True, "id": comp.id})


# ---------- admin ----------
@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_form(request: Request):
    require(request, "admin")
    with SessionLocal() as s:
        st = get_ws(s, active_ws_id(request))
        roles = await discord_api.get_guild_roles(st.guild_id) if st.guild_id else []
        return render(request, "admin_settings.html", settings=st, roles=roles)


@app.post("/admin/workspaces/new")
def workspace_new(request: Request, csrf: str = Form(""), name: str = Form("Nouveau serveur")):
    require(request, "admin")
    check_csrf(request, csrf)
    with SessionLocal() as s:
        ws = Workspace(name=name.strip() or "Nouveau serveur")
        s.add(ws)
        s.commit()
        wid, wname = ws.id, ws.name
    acc = dict(request.session.get("ws_access") or {})
    acc[str(wid)] = {"level": "admin", "role_ids": [], "name": wname}
    request.session["ws_access"] = acc
    request.session["ws"] = str(wid)
    return RedirectResponse("/admin/settings", status_code=303)


@app.post("/admin/settings")
async def admin_settings_save(request: Request):
    require(request, "admin")
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    with SessionLocal() as s:
        st = get_ws(s, active_ws_id(request))
        st.name = form.get("ws_name", "").strip() or st.name
        st.guild_id = form.get("guild_id", "").strip()
        st.calls_channel_id = form.get("calls_channel_id", "").strip()
        st.activities_channel_id = form.get("activities_channel_id", "").strip()
        st.timezone = form.get("timezone", "").strip() or "America/Toronto"
        st.call_mention = form.get("call_mention", "").strip()
        st.activity_mention = form.get("activity_mention", "").strip()
        # killboard Albion
        st.albion_region = form.get("albion_region", "").strip() or "Europe"
        st.albion_guild_id = form.get("albion_guild_id", "").strip()
        gname = form.get("albion_guild_name", "").strip()
        st.albion_guild_name = gname
        if gname and not st.albion_guild_id:  # cherche l'ID a partir du nom
            res = killboard.search_guilds(gname, st.albion_region)
            exact = [r for r in res if (r["name"] or "").lower() == gname.lower()]
            pick = exact[0] if exact else (res[0] if res else None)
            if pick:
                st.albion_guild_id = pick["id"] or ""
                st.albion_guild_name = pick["name"] or gname
        # n'ecrase le mapping que si le tableau des roles etait bien affiche,
        # sinon enregistrer juste l'ID du serveur effacerait tout
        if form.get("roles_rendered"):
            st.member_role_ids = form.getlist("member_roles")
            st.caller_role_ids = form.getlist("caller_roles")
            st.admin_role_ids = form.getlist("admin_roles")
        s.commit()
    return RedirectResponse("/admin/settings", status_code=303)


# ---------- killboard ----------
@app.get("/killboard", response_class=HTMLResponse)
def killboard_page(request: Request):
    require(request, "member")
    with SessionLocal() as s:
        st = get_ws(s, active_ws_id(request))
        gid, region, gname = st.albion_guild_id, st.albion_region, st.albion_guild_name
    kb = killboard.build_killboard(gid, region) if gid else None
    return render(request, "killboard.html", kb=kb, guild_name=gname,
                 region=region, guild_id=gid)


@app.get("/admin/killboard/search")
async def killboard_search(request: Request, q: str = "", region: str = "Europe"):
    require(request, "admin")
    return JSONResponse(killboard.search_guilds(q, region))


# ---------- profil & destiny board ----------
_BOARD = None


def load_board():
    global _BOARD
    if _BOARD is None:
        with open(os.path.join(BASE_DIR, "static", "destiny_board.json"), encoding="utf-8") as f:
            _BOARD = json.load(f)
    return _BOARD


_DESTINY_IDX = None


def _destiny_index():
    """(nom_arme_minuscule -> node_key, node_key -> [keys de la meme ligne])."""
    global _DESTINY_IDX
    if _DESTINY_IDX is None:
        name_key, line_of = {}, {}
        for a in load_board()["archetypes"]:
            for line in a["weapons"]:
                keys = [n["key"] for n in line["nodes"]]
                for n in line["nodes"]:
                    name_key[n["label"].lower()] = n["key"]
                    line_of[n["key"]] = keys
        _DESTINY_IDX = (name_key, line_of)
    return _DESTINY_IDX


def weapon_spec(specs, weapon_name):
    """Spec totale d'un joueur pour une arme = somme des specs de toute sa ligne.
    Ex: 100 + deux autres armes a 100 = 300. None si l'arme n'est pas reconnue."""
    if not weapon_name:
        return None
    name_key, line_of = _destiny_index()
    key = name_key.get(weapon_name.lower())
    if not key:
        return None
    specs = specs or {}
    return sum(int(specs.get(k, 0) or 0) for k in line_of.get(key, [key]))


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    user = require(request, "member")
    with SessionLocal() as s:
        u = s.get(User, user["id"])
        specs = u.specs or {}
    filled = len([v for v in specs.values() if v])
    return render(request, "profile.html", profile_user=user, specs_count=filled)


@app.get("/profile/destiny", response_class=HTMLResponse)
def destiny(request: Request):
    user = require(request, "member")
    with SessionLocal() as s:
        u = s.get(User, user["id"])
        specs = u.specs or {}
    return render(request, "destiny.html", board=load_board(), specs=specs)


@app.post("/profile/destiny")
async def destiny_save(request: Request):
    user = require(request, "member")
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    specs = {}
    for k, v in form.multi_items():
        if k.startswith("s_") and str(v).strip():
            try:
                lvl = max(0, min(100, int(v)))
            except (ValueError, TypeError):
                continue
            if lvl:
                specs[k[2:]] = lvl
    with SessionLocal() as s:
        u = s.get(User, user["id"])
        u.specs = specs
        s.commit()
    return RedirectResponse("/profile/destiny", status_code=303)


# ---------- API interne ----------
@app.get("/api/members")
async def api_members(request: Request):
    require(request, "caller")
    with SessionLocal() as s:
        st = get_ws(s, active_ws_id(request))
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
