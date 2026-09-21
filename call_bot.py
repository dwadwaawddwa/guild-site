#!/usr/bin/env python3
"""Bot Discord /call : cree un call depuis une compo du site et gere les inscriptions
(menus Choix 1/2/3 par priorite). Ecrit dans la meme base que le site (synchro)."""
import discord
from discord import app_commands
from discord.ext import tasks

from app import comp_image, config
from app.db import (Build, Call, CallPost, CallSignup, Composition, SessionLocal,
                   get_settings, get_ws, init_db, now, ws_for_guild)

MAX_CHOICES = 3

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# ---------- helpers DB ----------
def _slot_label(slot, i):
    bname = slot.build_name or slot.weapon or ""
    role = slot.role_name or ""
    label = (f"{role} — {bname}" if role and bname else role or bname or f"Slot {i+1}")
    return label[:100]


def comp_options(comp_id: int):
    """Retourne (list[SelectOption], mapping pos->{id,name}) depuis les slots de la compo."""
    opts, mapping = [], {}
    with SessionLocal() as s:
        comp = s.get(Composition, comp_id)
        if not comp:
            return opts, mapping
        for i, slot in enumerate(comp.slots[:25]):
            label = _slot_label(slot, i)
            mapping[str(i)] = {"id": slot.build_id or 0, "name": label}
            opts.append(discord.SelectOption(label=label, value=str(i)))
    return opts, mapping


def slot_choice(call_id: int, pos: str):
    """Recalcule le {id,name} d'une position de slot pour un call (robuste au redemarrage)."""
    with SessionLocal() as s:
        call = s.get(Call, call_id)
        if not call or not call.composition_id:
            return None
        comp = s.get(Composition, call.composition_id)
        try:
            slot = comp.slots[int(pos)]
        except (ValueError, IndexError):
            return None
        return {"id": f"s{int(pos)}", "name": _slot_label(slot, int(pos))}


def set_choices(call_id: int, user_id: str, username: str, choices: list[dict]):
    """Remplace tous les choix d'un membre (ordre = ordre de selection)."""
    with SessionLocal() as s:
        su = (s.query(CallSignup)
              .filter(CallSignup.call_id == call_id, CallSignup.user_id == user_id).first())
        if not su:
            su = CallSignup(call_id=call_id, user_id=user_id, username=username, status="in")
            s.add(su)
        su.build_choices = choices
        su.discord_choices = {str(i + 1): c for i, c in enumerate(choices)}
        su.build_id = None  # les refs de slot ne sont pas des ids de build
        su.build_name = choices[0]["name"] if choices else ""
        su.username = username
        s.commit()


def clear_signup(call_id: int, user_id: str):
    with SessionLocal() as s:
        su = (s.query(CallSignup)
              .filter(CallSignup.call_id == call_id, CallSignup.user_id == user_id).first())
        if su:
            s.delete(su)
            s.commit()


def _call_closed(call) -> bool:
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    return call.status in ("closed", "cancelled") or bool(call.end_at and call.end_at < now)


def build_embed(call_id: int) -> discord.Embed:
    with SessionLocal() as s:
        call = s.get(Call, call_id)
        comp = s.get(Composition, call.composition_id) if call.composition_id else None
        signups = list(call.signups)
        tname = call.type or "—"
        closed = _call_closed(call)
        rfinal = bool(call.roster_final and call.roster)
        prefix = "🔒 [CLÔTURÉ] " if closed else ("✅ [ROSTER CONFIRMÉ] " if rfinal else "📣 ")
        title = prefix + call.title
        is_zvz = "zvz" in (call.type or "").lower()
        color = 0x808080 if closed else (0x3FB950 if rfinal else (0xD4A24A if is_zvz else 0x4A86D4))
        e = discord.Embed(title=title, description=call.description or "", color=color)
        e.add_field(name="Type", value=tname, inline=True)
        if closed:
            e.add_field(name="Statut", value="🔒 Clôturé — inscriptions fermées", inline=True)
        elif rfinal:
            e.add_field(name="Statut", value="✅ Roster confirmé — définitif", inline=True)
        # horaires : timestamps Discord -> affiches dans le fuseau de chaque membre
        import datetime as _dt
        if call.scheduled_at:
            u = int(call.scheduled_at.replace(tzinfo=_dt.timezone.utc).timestamp())
            val = f"<t:{u}:F> (<t:{u}:R>)"
            if call.end_at:
                ue = int(call.end_at.replace(tzinfo=_dt.timezone.utc).timestamp())
                val += f"\n→ fin <t:{ue}:t>"
            e.add_field(name="🕒 Horaire", value=val, inline=False)
        if comp:
            e.add_field(name="Compo", value=comp.name, inline=True)
            e.add_field(name="Slots", value=str(len(comp.slots)), inline=True)
        # liens vers le site
        base = config.BASE_URL
        links = f"[📋 Ouvrir le call]({base}/calls/{call.id})"
        if comp:
            links += f" · [🧩 Voir la compo]({base}/compositions/{comp.id})"
        e.add_field(name="Sur le site", value=links, inline=False)
        # roster confirme (definitif) : affiche l'affectation figee (ping du membre)
        if rfinal:
            def _who(r):
                uid = str(r.get("user_id") or "")
                return f"<@{uid}>" if uid else (r.get("username") or "—")
            lines = [f"• **{r.get('label') or 'Place'}** → {_who(r)}"
                     for r in (call.roster or [])]
            e.add_field(name="🧭 Roster confirmé (définitif)",
                       value=("\n".join(lines) or "—")[:1024], inline=False)
        # sinon : places demandees + qui les a prises (✅) ou libres (⬜)
        elif comp and comp.slots:
            picks = {}
            for su in signups:
                for ch in (su.build_choices or []):
                    picks.setdefault(ch.get("id"), []).append(su.username)
            roster = []
            for i, slot in enumerate(comp.slots[:20]):
                who = picks.get(f"s{i}", [])
                bn = slot.build_name or slot.weapon or ""
                line = f"• **{slot.role_name or 'Rôle'}**" + (f" — {bn}" if bn else "")
                if who:
                    extra = f" +{len(who) - 3}" if len(who) > 3 else ""
                    line += f"  ✅ {', '.join(who[:3])}{extra}"
                else:
                    line += "  ⬜ *libre*"
                roster.append(line)
            e.add_field(name="Places (✅ pris · ⬜ libre)", value="\n".join(roster)[:1024], inline=False)
        if signups:
            lines = []
            for su in signups:
                choices = su.build_choices or []
                pref = " · ".join(f"P{i+1} {c['name']}" for i, c in enumerate(choices)) or "—"
                lines.append(f"**{su.username}** : {pref}")
            e.add_field(name=f"Inscrits ({len(signups)})", value="\n".join(lines)[:1024], inline=False)
        else:
            e.add_field(name="Inscrits",
                       value="Personne encore — ouvre le menu ci-dessous et coche 1 à 3 builds "
                             "(l'ordre de sélection = ton ordre de préférence).", inline=False)
        e.set_footer(text=f"Call #{call.id} · gere aussi sur le site")
        return e


# ---------- synchro multi-serveurs ----------
def _view_for_call(call_id: int) -> discord.ui.View:
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        comp_id = c.composition_id if c else None
    opts = comp_options(comp_id)[0] if comp_id else []
    return make_view(call_id, opts)


def _posted_targets(call_id: int):
    """Tous les messages Discord d'un call (multi-serveurs CallPost + legacy)."""
    seen, out = set(), []
    with SessionLocal() as s:
        for p in (s.query(CallPost)
                  .filter(CallPost.call_id == call_id, CallPost.message_id != "").all()):
            key = (p.channel_id, p.message_id)
            if key not in seen:
                seen.add(key)
                out.append((p.channel_id, p.message_id))
        c = s.get(Call, call_id)
        if c and c.discord_message_id:
            key = (c.discord_channel_id, c.discord_message_id)
            if key not in seen:
                out.append((c.discord_channel_id, c.discord_message_id))
    return out


async def _edit_message(chan_id, msg_id, embed, view=discord.utils.MISSING, file=None,
                        content=discord.utils.MISSING, allowed_mentions=None):
    try:
        channel = client.get_channel(int(chan_id)) or await client.fetch_channel(int(chan_id))
        msg = await channel.fetch_message(int(msg_id))
        kwargs = {"embed": embed, "view": view}
        if file is not None:  # remplace la piece jointe (image regeneree)
            kwargs["attachments"] = [file]
        if content is not discord.utils.MISSING:
            kwargs["content"] = content
        if allowed_mentions is not None:
            kwargs["allowed_mentions"] = allowed_mentions
        await msg.edit(**kwargs)
        return True
    except Exception as e:  # noqa
        print(f"Edit message {msg_id}: {e}")
        return False


def _regen_image_path(comp_id):
    """Regenere l'image PNG de la compo (avec les swaps) et retourne son chemin, ou None."""
    if not comp_id:
        return None
    try:
        with SessionLocal() as s:
            comp = s.get(Composition, comp_id)
            sections = comp_image.sections_from_comp(comp)
            cname = comp.name
        return comp_image.render_comp(cname, comp_image.GUILD_NAME, sections, f"comp_{comp_id}.png")
    except Exception as e:  # noqa
        print(f"Regen image compo {comp_id}: {e}")
        return None


async def update_all_posts(call_id: int, skip_message_id=None, refresh_image: bool = False,
                           ping: bool = False):
    """Met a jour l'embed sur tous les serveurs ou le call est poste.

    Retire le menu d'inscription quand le call est cloture ou le roster confirme (definitif).
    refresh_image=True regenere aussi l'image (pour montrer les swaps a jour).
    ping=True ajoute les mentions des membres retenus au contenu (notification unique).
    """
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        lock = bool(c and (_call_closed(c) or c.roster_final))
        comp_id = c.composition_id if c else None
        rfinal = bool(c and c.roster_final and c.roster)
        # roster confirme -> le message edite ping les membres retenus (une seule fois)
        content = discord.utils.MISSING
        am = None
        if ping and rfinal:
            seen, ment = set(), []
            for r in (c.roster or []):
                uid = str(r.get("user_id") or "")
                if uid and uid not in seen:
                    seen.add(uid)
                    ment.append(f"<@{uid}>")
            if ment:
                content = "🔔 Roster confirmé — convoqués : " + " ".join(ment)
                am = discord.AllowedMentions(users=True, everyone=False, roles=False)
    embed = build_embed(call_id)
    img_path = _regen_image_path(comp_id) if refresh_image else None
    if img_path:
        embed.set_image(url="attachment://compo.png")
    view = None if lock else discord.utils.MISSING
    for chan_id, msg_id in _posted_targets(call_id):
        if skip_message_id and str(msg_id) == str(skip_message_id):
            continue
        # une piece jointe ne peut servir qu'une fois -> nouveau File par message
        file = discord.File(img_path, filename="compo.png") if img_path else None
        await _edit_message(chan_id, msg_id, embed, view, file, content=content, allowed_mentions=am)


# ---------- composants UI (persistants) ----------
class ChoiceSelect(discord.ui.Select):
    """Un seul menu : coche 1 a 3 builds, l'ordre de selection = l'ordre de priorite."""

    def __init__(self, call_id: int, options: list[discord.SelectOption]):
        self.call_id = call_id
        super().__init__(
            placeholder="Choisis tes builds (1 à 3, dans l'ordre de préférence)",
            custom_id=f"callsel:{call_id}",
            min_values=1, max_values=min(MAX_CHOICES, len(options)), options=options)

    async def callback(self, interaction: discord.Interaction):
        choices = []
        for pos in self.values:
            ch = slot_choice(self.call_id, pos)
            if ch:
                choices.append(ch)
        set_choices(self.call_id, str(interaction.user.id),
                   interaction.user.display_name, choices)
        await interaction.response.edit_message(embed=build_embed(self.call_id))
        await update_all_posts(self.call_id, skip_message_id=interaction.message.id)
        pref = " · ".join(f"**{i + 1}.** {c['name']}" for i, c in enumerate(choices))
        await interaction.followup.send(f"✅ Choix enregistrés — {pref}", ephemeral=True)


class ClearButton(discord.ui.Button):
    def __init__(self, call_id: int):
        self.call_id = call_id
        super().__init__(label="Se désinscrire", style=discord.ButtonStyle.danger,
                        custom_id=f"callclear:{call_id}")

    async def callback(self, interaction: discord.Interaction):
        clear_signup(self.call_id, str(interaction.user.id))
        await interaction.response.edit_message(embed=build_embed(self.call_id))
        await update_all_posts(self.call_id, skip_message_id=interaction.message.id)
        await interaction.followup.send("🚪 Tu es désinscrit de ce call.", ephemeral=True)


def make_view(call_id: int, options: list[discord.SelectOption]) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    if options:
        v.add_item(ChoiceSelect(call_id, options))
    v.add_item(ClearButton(call_id))
    return v


# ---------- permissions ----------
def is_caller(interaction: discord.Interaction) -> bool:
    if str(interaction.user.id) in config.ADMIN_BOOTSTRAP_IDS:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    if perms and perms.administrator:
        return True
    with SessionLocal() as s:
        st = ws_for_guild(s, interaction.guild_id)
        allowed = set(map(str, (st.caller_role_ids or []))) | set(map(str, (st.admin_role_ids or [])))
    role_ids = {str(r.id) for r in getattr(interaction.user, "roles", [])}
    return bool(role_ids & allowed)


# ---------- commande /call ----------
async def compo_autocomplete(interaction: discord.Interaction, current: str):
    with SessionLocal() as s:
        wsid = ws_for_guild(s, interaction.guild_id).id
        q = s.query(Composition).filter(
            (Composition.workspace_id == wsid) | (Composition.public == True))
        if current:
            q = q.filter(Composition.name.ilike(f"%{current}%"))
        comps = q.order_by(Composition.name).limit(25).all()
        return [app_commands.Choice(name=c.name, value=c.name) for c in comps]


def parse_local_time(text: str, tzname: str):
    """'25/07 20:00' (fuseau de la guilde) -> datetime UTC naif. None si illisible."""
    import datetime as _dt
    if not text:
        return None
    text = text.strip().replace("h", ":") if "h" in text.lower() and ":" not in text else text.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m %H:%M", "%Y-%m-%d %H:%M"):
        try:
            d = _dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = d.replace(year=_dt.datetime.now().year)
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(tzname)
        except Exception:  # noqa
            tz = _dt.timezone.utc
        return d.replace(tzinfo=tz).astimezone(_dt.timezone.utc).replace(tzinfo=None)
    return None


@tree.command(name="call", description="Créer un call à partir d'une composition du site")
@app_commands.describe(nom="Nom du call", compo="Nom de la composition (site)",
                      description="Description (optionnel)",
                      debut="Heure de début, ex: 25/07 20:00 (fuseau de la guilde)",
                      fin="Heure de fin, ex: 25/07 22:00 (optionnel)")
@app_commands.autocomplete(compo=compo_autocomplete)
async def call_cmd(interaction: discord.Interaction, nom: str, compo: str,
                  description: str = "", debut: str = "", fin: str = ""):
    if not is_caller(interaction):
        await interaction.response.send_message(
            "⛔ Réservé aux callers/admins.", ephemeral=True)
        return
    with SessionLocal() as s:
        st = ws_for_guild(s, interaction.guild_id)
        wsid = st.id
        tzname = st.timezone or "America/Toronto"
        comp = (s.query(Composition)
               .filter(Composition.name.ilike(compo))
               .filter((Composition.workspace_id == wsid) | (Composition.public == True))
               .first())
        if not comp:
            await interaction.response.send_message(
                f"Composition « {compo} » introuvable sur ce serveur.", ephemeral=True)
            return
        comp_id = comp.id
        comp_type = comp.content_type
        c = Call(title=nom.strip(), description=description.strip(), type=comp_type,
                scheduled_at=parse_local_time(debut, tzname),
                end_at=parse_local_time(fin, tzname), workspace_id=wsid,
                composition_id=comp_id, created_by=str(interaction.user.id),
                created_by_name=interaction.user.display_name, status="open")
        s.add(c)
        s.commit()
        call_id = c.id
    options, _ = comp_options(comp_id)
    view = make_view(call_id, options)

    # image de la compo (style albioncompo) attachee au message
    embed = build_embed(call_id)
    file = None
    try:
        with SessionLocal() as s:
            comp = s.get(Composition, comp_id)
            sections = comp_image.sections_from_comp(comp)
            cname = comp.name
        path = comp_image.render_comp(cname, comp_image.GUILD_NAME, sections, f"comp_{comp_id}.png")
        file = discord.File(path, filename="compo.png")
        embed.set_image(url="attachment://compo.png")
    except Exception as e:  # l'image ne doit jamais bloquer la creation du call
        print(f"Image compo indisponible: {e}")

    with SessionLocal() as s:
        mention = (ws_for_guild(s, interaction.guild_id).call_mention or "").strip()
    am = discord.AllowedMentions(everyone=True, roles=True, users=True)
    kwargs = {"embed": embed, "view": view, "allowed_mentions": am}
    if mention:
        kwargs["content"] = mention
    if file:
        kwargs["file"] = file
    await interaction.response.send_message(**kwargs)
    msg = await interaction.original_response()
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        c.discord_message_id = str(msg.id)
        c.discord_channel_id = str(msg.channel.id)
        # trace aussi comme CallPost (synchro multi-serveurs uniforme)
        s.add(CallPost(call_id=call_id, workspace_id=c.workspace_id or 1,
                       channel_id=str(msg.channel.id), message_id=str(msg.id),
                       needs_post=False))
        s.commit()


async def _send_to_channel(channel, title: str, kwargs: dict):
    """Envoie le message. Salon Forum -> cree un POST (thread) ; salon texte -> message normal.

    Retourne le message de depart (son .channel est le thread dans le cas d'un forum).
    """
    if isinstance(channel, discord.ForumChannel):
        # certains forums exigent une etiquette : on en applique une par defaut
        if getattr(channel.flags, "require_tag", False) and channel.available_tags:
            kwargs["applied_tags"] = [channel.available_tags[0]]
        created = await channel.create_thread(name=title[:100], **kwargs)
        return created.message  # created.thread == le post cree
    return await channel.send(**kwargs)


async def _post_call(channel, call_id: int, comp_id, post_id, target_ws):
    """Poste un call sur Discord (embed + image + menu) et enregistre le CallPost."""
    options = comp_options(comp_id)[0] if comp_id else []
    view = make_view(call_id, options)
    embed = build_embed(call_id)
    file = None
    try:
        if comp_id:
            with SessionLocal() as s:
                comp = s.get(Composition, comp_id)
                sections = comp_image.sections_from_comp(comp)
                cname = comp.name
            path = comp_image.render_comp(cname, comp_image.GUILD_NAME, sections, f"comp_{comp_id}.png")
            file = discord.File(path, filename="compo.png")
            embed.set_image(url="attachment://compo.png")
    except Exception as e:  # noqa
        print(f"Image compo indisponible: {e}")
    # mention selon le serveur cible (chaque serveur a sa propre mention)
    with SessionLocal() as s:
        c = s.get(Call, call_id)
        st = get_ws(s, target_ws)
        mention = ((st.activity_mention if (c and c.is_activity) else st.call_mention) or "").strip()
        ctitle = (c.title if c else "") or "Call"
    am = discord.AllowedMentions(everyone=True, roles=True, users=True)
    kwargs = {"embed": embed, "view": view, "allowed_mentions": am}
    if mention:
        kwargs["content"] = mention
    if file:
        kwargs["file"] = file
    msg = await _send_to_channel(channel, ctitle, kwargs)
    with SessionLocal() as s:
        p = s.get(CallPost, post_id)
        if p:
            p.channel_id = str(msg.channel.id)
            p.message_id = str(msg.id)
            p.needs_post = False
        c = s.get(Call, call_id)
        if c and not c.discord_message_id:  # premier post -> renseigne legacy
            c.discord_message_id = str(msg.id)
            c.discord_channel_id = str(msg.channel.id)
        s.commit()


async def _resolve_channel(chan_id):
    if not chan_id:
        return None
    ch = client.get_channel(int(chan_id))
    if ch is None:
        try:
            ch = await client.fetch_channel(int(chan_id))
        except Exception:  # noqa
            return None
    return ch


@tasks.loop(seconds=12)
async def post_pending_calls():
    """Poste chaque CallPost en attente dans le salon de son serveur cible."""
    with SessionLocal() as s:
        pending = []
        for p in (s.query(CallPost)
                  .filter(CallPost.needs_post == True, CallPost.message_id == "").all()):
            c = s.get(Call, p.call_id)
            if c:
                pending.append((p.id, c.id, c.composition_id, bool(c.is_activity), p.workspace_id or 1))
    for post_id, cid, comp_id, is_act, wsid in pending:
        with SessionLocal() as s:
            ws = get_ws(s, wsid)
            chan_id = ws.activities_channel_id if is_act else ws.calls_channel_id
        channel = await _resolve_channel(chan_id)
        if channel is None:  # pas de salon configure -> ne pas reessayer en boucle
            with SessionLocal() as s:
                p = s.get(CallPost, post_id)
                if p:
                    p.needs_post = False
                    s.commit()
            continue
        try:
            await _post_call(channel, cid, comp_id, post_id, wsid)
        except Exception as e:  # noqa
            print(f"Echec post call {cid} (ws {wsid}): {e}")
            with SessionLocal() as s:  # evite de reessayer en boucle
                p = s.get(CallPost, post_id)
                if p:
                    p.needs_post = False
                    s.commit()


@tree.command(name="salon", description="Définit CE salon comme salon des calls (calls créés sur le site)")
async def salon_cmd(interaction: discord.Interaction):
    if not is_caller(interaction):
        await interaction.response.send_message("⛔ Réservé aux callers/admins.", ephemeral=True)
        return
    with SessionLocal() as s:
        st = ws_for_guild(s, interaction.guild_id)
        st.calls_channel_id = str(interaction.channel_id)
        s.commit()
    await interaction.response.send_message(
        f"✅ Les calls créés sur le site seront désormais postés dans <#{interaction.channel_id}>.",
        ephemeral=True)


@tree.command(name="salon_activite", description="Définit CE salon comme salon des activités (membres)")
async def salon_activite_cmd(interaction: discord.Interaction):
    if not is_caller(interaction):
        await interaction.response.send_message("⛔ Réservé aux callers/admins.", ephemeral=True)
        return
    with SessionLocal() as s:
        st = ws_for_guild(s, interaction.guild_id)
        st.activities_channel_id = str(interaction.channel_id)
        s.commit()
    await interaction.response.send_message(
        f"✅ Les activités créées sur le site seront désormais postées dans <#{interaction.channel_id}>.",
        ephemeral=True)


@tasks.loop(seconds=30)
async def update_closed_calls():
    """Met a jour tous les messages Discord des calls devenus clotures/termines."""
    with SessionLocal() as s:
        closed_ids = [c.id for c in s.query(Call)
                      .filter(Call.discord_closed == False).all()
                      if _call_closed(c) and _posted_targets(c.id)]
    for cid in closed_ids:
        await update_all_posts(cid, refresh_image=True)  # retire le menu + image a jour
        with SessionLocal() as s:  # marque comme traite (evite de reessayer en boucle)
            c = s.get(Call, cid)
            if c:
                c.discord_closed = True
                s.commit()


@tasks.loop(seconds=12)
async def sync_rosters():
    """Le caller a confirme un roster sur le site -> met a jour Discord + ping les membres."""
    with SessionLocal() as s:
        ids = [c.id for c in s.query(Call)
               .filter(Call.roster_final == True, Call.roster_synced == False).all()]
    for cid in ids:
        # met a jour l'embed + ping les membres retenus DANS le message edite
        await update_all_posts(cid, refresh_image=True, ping=True)
        with SessionLocal() as s:
            c = s.get(Call, cid)
            if c:
                c.roster_synced = True
                s.commit()


@client.event
async def on_ready():
    init_db()
    from app.db import all_workspaces
    with SessionLocal() as s:
        guild_ids = [w.guild_id for w in all_workspaces(s) if w.guild_id]
        posted_ids = {p.call_id for p in s.query(CallPost).filter(CallPost.message_id != "").all()}
        open_calls = s.query(Call).filter(Call.status == "open").all()
        pending = [(c.id, c.composition_id) for c in open_calls
                   if c.discord_message_id or c.id in posted_ids]
    for cid, comp_id in pending:
        opts = []
        if comp_id:
            opts, _ = comp_options(comp_id)
        client.add_view(make_view(cid, opts))
    if not post_pending_calls.is_running():
        post_pending_calls.start()
    if not update_closed_calls.is_running():
        update_closed_calls.start()
    if not sync_rosters.is_running():
        sync_rosters.start()
    # synchronise les slash commands sur CHAQUE serveur configure
    for gid in set(guild_ids):
        try:
            guild = discord.Object(id=int(gid))
            tree.copy_global_to(guild=guild)
            await tree.sync(guild=guild)
        except Exception as e:  # noqa
            print(f"Sync commandes echoue pour {gid}: {e}")
    print(f"call-bot pret : {client.user} — commandes sync sur {len(set(guild_ids))} serveur(s)")
    # rafraichissement UNIQUE des images des calls deja postes (pour afficher les swaps)
    import os as _os
    marker = _os.path.join(comp_image.BASE_DIR, "static", ".img_refreshed_v1")
    if not _os.path.exists(marker):
        refreshed = 0
        for cid, comp_id in pending:
            if comp_id and _posted_targets(cid):
                await update_all_posts(cid, refresh_image=True)
                refreshed += 1
        try:
            with open(marker, "w") as f:
                f.write("done")
        except Exception:  # noqa
            pass
        print(f"Images rafraichies (swaps) sur {refreshed} call(s) deja poste(s)")


if __name__ == "__main__":
    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN manquant")
    client.run(config.DISCORD_BOT_TOKEN)
