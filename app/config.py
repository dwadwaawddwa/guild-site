"""Configuration chargee depuis l'environnement (.env)."""
import os
import secrets

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Identifiants Discord (application unique : OAuth + bot)
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# URL publique de base (LAN/IP pour l'instant, domaine + https plus tard)
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
REDIRECT_URI = BASE_URL + "/auth/callback"

# Secret de session (genere si absent ; mets une valeur fixe en prod dans .env)
SESSION_SECRET = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # passe a 1 en HTTPS

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'guild.db')}")

# IDs Discord toujours admin (bootstrap), separes par des virgules
ADMIN_BOOTSTRAP_IDS = {x.strip() for x in os.getenv("ADMIN_BOOTSTRAP_IDS", "").split(",") if x.strip()}

DISCORD_API = "https://discord.com/api/v10"
OAUTH_SCOPE = "identify"


def is_configured() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and DISCORD_BOT_TOKEN)
