"""Appels a l'API Discord (OAuth + bot)."""
import httpx

from . import config

BOT_HEADERS = {"Authorization": f"Bot {config.DISCORD_BOT_TOKEN}"}


def authorize_url(state: str) -> str:
    from urllib.parse import urlencode
    params = {
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": config.REDIRECT_URI,
        "response_type": "code",
        "scope": config.OAUTH_SCOPE,
        "state": state,
        "prompt": "consent",
    }
    return f"{config.DISCORD_API}/oauth2/authorize?" + urlencode(params)


async def exchange_code(code: str) -> dict:
    data = {
        "client_id": config.DISCORD_CLIENT_ID,
        "client_secret": config.DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.REDIRECT_URI,
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{config.DISCORD_API}/oauth2/token", data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        return r.json()


async def get_current_user(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{config.DISCORD_API}/users/@me",
                        headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        return r.json()


async def get_guild_roles(guild_id: str) -> list[dict]:
    """Liste des roles du serveur (via le bot). [] si echec."""
    if not (guild_id and config.DISCORD_BOT_TOKEN):
        return []
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{config.DISCORD_API}/guilds/{guild_id}/roles", headers=BOT_HEADERS)
        if r.status_code != 200:
            return []
        roles = r.json()
        roles.sort(key=lambda x: x.get("position", 0), reverse=True)
        return roles


async def get_member(guild_id: str, user_id: str) -> dict | None:
    """Membre du serveur (via le bot), ou None si absent/echec."""
    if not (guild_id and config.DISCORD_BOT_TOKEN):
        return None
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{config.DISCORD_API}/guilds/{guild_id}/members/{user_id}",
                        headers=BOT_HEADERS)
        if r.status_code != 200:
            return None
        return r.json()


async def list_members(guild_id: str, limit: int = 1000) -> list[dict]:
    """Membres du serveur (necessite Server Members Intent)."""
    if not (guild_id and config.DISCORD_BOT_TOKEN):
        return []
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{config.DISCORD_API}/guilds/{guild_id}/members",
                        headers=BOT_HEADERS, params={"limit": limit})
        if r.status_code != 200:
            return []
        return r.json()
