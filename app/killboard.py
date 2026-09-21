"""Killboard : interroge l'API gameinfo d'Albion Online (avec cache)."""
import json
import time
import urllib.parse
import urllib.request

REGION_HOSTS = {
    "Europe": "https://gameinfo-ams.albiononline.com",
    "Americas": "https://gameinfo.albiononline.com",
    "Asia": "https://gameinfo-sgp.albiononline.com",
}
UA = {"User-Agent": "Mozilla/5.0 (dwa.gold killboard)"}

_CACHE = {}          # url -> (timestamp, data)
_TTL = 600           # 10 minutes


def _host(region: str) -> str:
    return REGION_HOSTS.get(region, REGION_HOSTS["Europe"])


def _get(url: str, ttl: int = _TTL):
    now = time.time()
    hit = _CACHE.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        req = urllib.request.Request(url, headers=UA)
        data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    except Exception:  # noqa
        return hit[1] if hit else None  # sert le cache perime si dispo
    _CACHE[url] = (now, data)
    return data


def search_guilds(name: str, region: str):
    if not name:
        return []
    url = f"{_host(region)}/api/gameinfo/search?q=" + urllib.parse.quote(name)
    d = _get(url, ttl=300) or {}
    return [{"id": g.get("Id"), "name": g.get("Name")} for g in (d.get("guilds") or [])]


def guild_info(guild_id: str, region: str):
    if not guild_id:
        return None
    return _get(f"{_host(region)}/api/gameinfo/guilds/{guild_id}")


def guild_members(guild_id: str, region: str):
    if not guild_id:
        return []
    return _get(f"{_host(region)}/api/gameinfo/guilds/{guild_id}/members") or []


def recent_kills(guild_id: str, region: str, limit: int = 30):
    if not guild_id:
        return []
    url = (f"{_host(region)}/api/gameinfo/events?guildId={guild_id}"
          f"&limit={limit}&offset=0")
    return _get(url, ttl=180) or []


def build_killboard(guild_id: str, region: str):
    """Assemble les donnees du killboard : totaux + membres tries + kills recents."""
    info = guild_info(guild_id, region) or {}
    members = guild_members(guild_id, region) or []
    kills = recent_kills(guild_id, region) or []

    rows = []
    for m in members:
        kf = m.get("KillFame", 0) or 0
        df = m.get("DeathFame", 0) or 0
        rows.append({
            "name": m.get("Name", "?"),
            "kill_fame": kf,
            "death_fame": df,
            "ratio": (round(kf / df, 2) if df else (None if kf else 0)),  # None = infini
            "ip": round(m.get("AverageItemPower", 0) or 0),
        })
    rows.sort(key=lambda r: r["kill_fame"], reverse=True)

    kill_feed = []
    for k in kills[:30]:
        killer = (k.get("Killer") or {})
        victim = (k.get("Victim") or {})
        kill_feed.append({
            "killer": killer.get("Name", "?"),
            "killer_guild": killer.get("GuildName", ""),
            "victim": victim.get("Name", "?"),
            "victim_guild": victim.get("GuildName", ""),
            "fame": k.get("TotalVictimKillFame", 0) or 0,
            "ip": round(victim.get("AverageItemPower", 0) or 0),
            "time": k.get("TimeStamp", ""),
        })

    total_kf = sum(r["kill_fame"] for r in rows) or info.get("KillFame", 0)
    total_df = sum(r["death_fame"] for r in rows) or info.get("DeathFame", 0)
    return {
        "name": info.get("Name") or "",
        "member_count": len(members),
        "total_kill_fame": total_kf,
        "total_death_fame": total_df,
        "ratio": round(total_kf / total_df, 2) if total_df else 0,
        "members": rows,
        "kills": kill_feed,
    }
