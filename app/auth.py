"""Logique d'authentification : calcul du niveau d'acces depuis les roles Discord."""
from . import config, discord_api
from .db import all_workspaces

LEVELS = {"guest": 0, "member": 1, "caller": 2, "admin": 3}


def compute_access_level(role_ids: list[str], settings, is_guild_admin: bool = False) -> str:
    role_ids = set(str(r) for r in (role_ids or []))
    admin = set(str(r) for r in (settings.admin_role_ids or []))
    caller = set(str(r) for r in (settings.caller_role_ids or []))
    member = set(str(r) for r in (settings.member_role_ids or []))
    if is_guild_admin or (role_ids & admin):
        return "admin"
    if role_ids & caller:
        return "caller"
    if role_ids & member:
        return "member"
    # si aucun role membre n'est configure, tout membre du serveur est "member"
    if not member and role_ids is not None:
        return "member"
    return "guest"


async def sync_user_workspaces(session, discord_user: dict) -> dict:
    """Calcule l'acces du membre dans CHAQUE serveur.
    Retourne {ws_id(str): {level, role_ids, name}} pour les serveurs ou il a acces."""
    uid = str(discord_user["id"])
    is_boot = uid in config.ADMIN_BOOTSTRAP_IDS
    access = {}
    for ws in all_workspaces(session):
        role_ids = []
        if ws.guild_id:
            member = await discord_api.get_member(ws.guild_id, uid)
            if member is None:
                if is_boot:
                    access[str(ws.id)] = {"level": "admin", "role_ids": [], "name": ws.name}
                continue
            role_ids = [str(r) for r in member.get("roles", [])]
            level = "admin" if is_boot else compute_access_level(role_ids, ws)
        else:
            # serveur sans guild configure : accessible aux connectes (membre)
            level = "admin" if is_boot else "member"
        if level != "guest":
            access[str(ws.id)] = {"level": level, "role_ids": role_ids, "name": ws.name}
    return access


def has_level(user: dict | None, required: str) -> bool:
    if not user:
        return False
    return LEVELS.get(user.get("access_level", "guest"), 0) >= LEVELS[required]
