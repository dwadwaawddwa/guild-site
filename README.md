# DWAlbion builder

Site pour organiser une guilde Albion Online → [guild.dwa.gold](https://guild.dwa.gold)

- Calls (small scale / ZvZ) avec inscriptions par priorité.
- Compositions et builds avec images générées, page de détail par build.
- Killboard, plateau Destiny, base d'items Albion.
- Connexion Discord, rôles membre / caller / admin lus sur le serveur Discord.
- Plusieurs serveurs (workspaces) ; mode invité qui ne voit que les builds, compos et images.
- `call_bot.py` : bot Discord `/call` qui crée un call depuis une compo du site et gère les inscriptions (même base que le site).

## Stack

Python · FastAPI · Uvicorn · SQLAlchemy · SQLite · Jinja2 · Authlib · discord.py

## Lancer en local

Créer un `.env` avec : `BASE_URL`, `SESSION_SECRET`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, `COOKIE_SECURE`, `DATABASE_URL` (facultatif), `ADMIN_BOOTSTRAP_IDS` (facultatif).

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python -m uvicorn app.main:app --port 8000
```

Puis renseigner le serveur Discord et les rôles dans `/admin/settings`. `tools/` régénère les données d'items et du plateau Destiny.
