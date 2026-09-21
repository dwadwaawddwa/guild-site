"""Modeles SQLAlchemy et session DB."""
import datetime as dt

from sqlalchemy import (JSON, Boolean, Column, DateTime, ForeignKey, Integer,
                        String, Text, create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from . import config

engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    guild_id = Column(String, default="")
    calls_channel_id = Column(String, default="")       # salon des calls (officiels)
    activities_channel_id = Column(String, default="")  # salon des activites (membres)
    timezone = Column(String, default="America/Toronto")  # fuseau de reference pour /call
    albion_guild_id = Column(String, default="")     # ID de la guilde (API gameinfo)
    albion_guild_name = Column(String, default="")   # nom de la guilde (recherche)
    albion_region = Column(String, default="Europe")  # Europe|Americas|Asia
    call_mention = Column(String, default="")      # mention auto sur les calls (ex: @here, <@&roleid>)
    activity_mention = Column(String, default="")  # mention auto sur les activites
    # listes d'IDs de roles Discord (JSON list[str])
    member_role_ids = Column(JSON, default=list)
    caller_role_ids = Column(JSON, default=list)
    admin_role_ids = Column(JSON, default=list)


class Workspace(Base):
    """Un serveur Discord servi par le site (config + contenu scopes)."""
    __tablename__ = "workspaces"
    id = Column(Integer, primary_key=True)
    name = Column(String, default="Serveur")
    guild_id = Column(String, default="")
    member_role_ids = Column(JSON, default=list)
    caller_role_ids = Column(JSON, default=list)
    admin_role_ids = Column(JSON, default=list)
    calls_channel_id = Column(String, default="")
    activities_channel_id = Column(String, default="")
    call_mention = Column(String, default="")
    activity_mention = Column(String, default="")
    timezone = Column(String, default="America/Toronto")
    albion_guild_id = Column(String, default="")
    albion_guild_name = Column(String, default="")
    albion_region = Column(String, default="Europe")
    created_at = Column(DateTime, default=now)


class User(Base):
    __tablename__ = "users"
    discord_id = Column(String, primary_key=True)
    username = Column(String, default="")
    global_name = Column(String, default="")
    avatar = Column(String, default="")
    access_level = Column(String, default="guest")  # guest|member|caller|admin
    role_ids = Column(JSON, default=list)
    specs = Column(JSON, default=dict)  # Destiny Board : {node_key: niveau 0-100}
    last_login = Column(DateTime, default=now)


class Call(Base):
    __tablename__ = "calls"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, default=1)
    type = Column(String, default="small_scale")  # small_scale|zvz
    title = Column(String, default="")
    description = Column(Text, default="")
    scheduled_at = Column(DateTime, nullable=True)   # debut (UTC naif)
    end_at = Column(DateTime, nullable=True)         # fin (UTC naif)
    status = Column(String, default="open")  # open|closed|cancelled
    created_by = Column(String, default="")
    created_by_name = Column(String, default="")
    is_activity = Column(Boolean, default=False)  # cree par un membre (hors salon des calls)
    composition_id = Column(Integer, ForeignKey("compositions.id"), nullable=True)
    discord_message_id = Column(String, default="")
    discord_channel_id = Column(String, default="")
    needs_discord_post = Column(Boolean, default=False)  # a poster sur Discord (call cree sur le site)
    discord_closed = Column(Boolean, default=False)      # message Discord deja mis a jour en "cloture"
    roster = Column(JSON, default=list)                  # roster final : [{ref,label,user_id,username}]
    roster_final = Column(Boolean, default=False)        # roster confirme (definitif) par le caller
    roster_synced = Column(Boolean, default=False)       # message Discord deja mis a jour avec le roster
    created_at = Column(DateTime, default=now)

    composition = relationship("Composition")
    signups = relationship("CallSignup", back_populates="call",
                           cascade="all, delete-orphan")
    posts = relationship("CallPost", back_populates="call",
                        cascade="all, delete-orphan")


class CallPost(Base):
    """Un message Discord d'un call, dans un serveur donne (cross-posting)."""
    __tablename__ = "call_posts"
    id = Column(Integer, primary_key=True)
    call_id = Column(Integer, ForeignKey("calls.id"))
    workspace_id = Column(Integer, default=1)
    channel_id = Column(String, default="")
    message_id = Column(String, default="")
    needs_post = Column(Boolean, default=True)
    closed = Column(Boolean, default=False)

    call = relationship("Call", back_populates="posts")


class CallSignup(Base):
    __tablename__ = "call_signups"
    id = Column(Integer, primary_key=True)
    call_id = Column(Integer, ForeignKey("calls.id"))
    user_id = Column(String, default="")
    username = Column(String, default="")
    slot_role = Column(String, default="")
    build_id = Column(Integer, ForeignKey("builds.id"), nullable=True)
    build_name = Column(String, default="")
    build_choices = Column(JSON, default=list)  # [{"id":..,"name":..}] par ordre de priorite
    discord_choices = Column(JSON, default=dict)  # {"1":{id,name},"2":..,"3":..} (source du bot)
    status = Column(String, default="in")  # in|tentative|out
    created_at = Column(DateTime, default=now)

    call = relationship("Call", back_populates="signups")
    build = relationship("Build")


class Build(Base):
    __tablename__ = "builds"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, default=1)
    public = Column(Boolean, default=False)  # visible depuis les autres serveurs
    name = Column(String, default="")
    category = Column(String, default="")  # tank|dps_melee|dps_range|support|heal
    weapon = Column(String, default="")
    offhand = Column(String, default="")
    head = Column(String, default="")
    chest = Column(String, default="")
    shoes = Column(String, default="")
    cape = Column(String, default="")
    food = Column(String, default="")
    potion = Column(String, default="")
    spells = Column(JSON, default=dict)  # {q, w, e, passive, head, chest, shoes}
    swaps = Column(JSON, default=dict)   # {slot: nom_item_alternatif} (arme/armure/off-hand de swap)
    notes = Column(Text, default="")
    created_by = Column(String, default="")
    created_by_name = Column(String, default="")
    official = Column(Boolean, default=True)  # cree par caller/admin
    created_at = Column(DateTime, default=now)


class Composition(Base):
    __tablename__ = "compositions"
    id = Column(Integer, primary_key=True)
    workspace_id = Column(Integer, default=1)
    public = Column(Boolean, default=False)  # visible depuis les autres serveurs
    name = Column(String, default="")
    content_type = Column(String, default="small_scale")  # small_scale|zvz
    notes = Column(Text, default="")
    created_by = Column(String, default="")
    created_by_name = Column(String, default="")
    official = Column(Boolean, default=True)  # cree par caller/admin
    created_at = Column(DateTime, default=now)

    slots = relationship("CompSlot", back_populates="composition",
                        cascade="all, delete-orphan", order_by="CompSlot.position")


class CompSlot(Base):
    __tablename__ = "comp_slots"
    id = Column(Integer, primary_key=True)
    comp_id = Column(Integer, ForeignKey("compositions.id"))
    position = Column(Integer, default=0)
    role_name = Column(String, default="")
    category = Column(String, default="")  # tank|dps_melee|dps_range|support|heal
    build_name = Column(String, default="")
    weapon = Column(String, default="")
    offhand = Column(String, default="")
    head = Column(String, default="")
    chest = Column(String, default="")
    shoes = Column(String, default="")
    cape = Column(String, default="")
    food = Column(String, default="")
    potion = Column(String, default="")
    swaps = Column(JSON, default=dict)   # {slot: item alternatif} recopie du build
    build_id = Column(Integer, ForeignKey("builds.id"), nullable=True)
    assigned_user_id = Column(String, default="")
    assigned_username = Column(String, default="")

    composition = relationship("Composition", back_populates="slots")
    build = relationship("Build")


def _migrate():
    """Ajoute les colonnes manquantes aux tables SQLite existantes (migration legere)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    wanted = {
        "call_signups": {
            "build_id": "INTEGER",
            "build_name": "VARCHAR DEFAULT ''",
            "build_choices": "JSON",
            "discord_choices": "JSON",
        },
        "calls": {
            "discord_message_id": "VARCHAR DEFAULT ''",
            "discord_channel_id": "VARCHAR DEFAULT ''",
            "end_at": "DATETIME",
            "needs_discord_post": "BOOLEAN DEFAULT 0",
            "discord_closed": "BOOLEAN DEFAULT 0",
            "created_by_name": "VARCHAR DEFAULT ''",
            "is_activity": "BOOLEAN DEFAULT 0",
            "workspace_id": "INTEGER DEFAULT 1",
            "roster": "JSON",
            "roster_final": "BOOLEAN DEFAULT 0",
            "roster_synced": "BOOLEAN DEFAULT 0",
        },
        "settings": {
            "calls_channel_id": "VARCHAR DEFAULT ''",
            "activities_channel_id": "VARCHAR DEFAULT ''",
            "timezone": "VARCHAR DEFAULT 'America/Toronto'",
            "albion_guild_id": "VARCHAR DEFAULT ''",
            "albion_guild_name": "VARCHAR DEFAULT ''",
            "albion_region": "VARCHAR DEFAULT 'Europe'",
            "call_mention": "VARCHAR DEFAULT ''",
            "activity_mention": "VARCHAR DEFAULT ''",
        },
        "builds": {
            "category": "VARCHAR DEFAULT ''",
            "swaps": "JSON",
            "created_by_name": "VARCHAR DEFAULT ''",
            "official": "BOOLEAN DEFAULT 1",
            "workspace_id": "INTEGER DEFAULT 1",
            "public": "BOOLEAN DEFAULT 0",
        },
        "compositions": {
            "created_by_name": "VARCHAR DEFAULT ''",
            "official": "BOOLEAN DEFAULT 1",
            "workspace_id": "INTEGER DEFAULT 1",
            "public": "BOOLEAN DEFAULT 0",
        },
        "users": {
            "specs": "JSON",
        },
        "comp_slots": {
            "category": "VARCHAR DEFAULT ''",
            "build_name": "VARCHAR DEFAULT ''",
            "weapon": "VARCHAR DEFAULT ''",
            "offhand": "VARCHAR DEFAULT ''",
            "head": "VARCHAR DEFAULT ''",
            "chest": "VARCHAR DEFAULT ''",
            "shoes": "VARCHAR DEFAULT ''",
            "cape": "VARCHAR DEFAULT ''",
            "food": "VARCHAR DEFAULT ''",
            "potion": "VARCHAR DEFAULT ''",
            "swaps": "JSON",
        },
    }
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for col, ddl in cols.items():
                if col not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {ddl}'))


def init_db():
    Base.metadata.create_all(engine)
    _migrate()
    with SessionLocal() as s:
        st = s.get(Settings, 1)
        if not st:
            st = Settings(id=1, member_role_ids=[], caller_role_ids=[], admin_role_ids=[])
            s.add(st)
            s.commit()
        # serveur #1 cree depuis les anciens reglages (une seule fois)
        if not s.get(Workspace, 1):
            s.add(Workspace(
                id=1, name="Serveur principal", guild_id=st.guild_id or "",
                member_role_ids=st.member_role_ids or [], caller_role_ids=st.caller_role_ids or [],
                admin_role_ids=st.admin_role_ids or [],
                calls_channel_id=st.calls_channel_id or "",
                activities_channel_id=st.activities_channel_id or "",
                call_mention=st.call_mention or "", activity_mention=st.activity_mention or "",
                timezone=st.timezone or "America/Toronto",
                albion_guild_id=st.albion_guild_id or "", albion_guild_name=st.albion_guild_name or "",
                albion_region=st.albion_region or "Europe"))
            s.commit()


def get_settings(session) -> "Settings":
    st = session.get(Settings, 1)
    if not st:
        st = Settings(id=1, member_role_ids=[], caller_role_ids=[], admin_role_ids=[])
        session.add(st)
        session.commit()
    return st


def get_ws(session, ws_id) -> "Workspace":
    return session.get(Workspace, int(ws_id) if ws_id else 1) or session.get(Workspace, 1)


def all_workspaces(session):
    return session.query(Workspace).order_by(Workspace.id).all()


def ws_for_guild(session, guild_id):
    """Serveur (Workspace) correspondant a un guild Discord ; defaut #1."""
    if guild_id:
        w = session.query(Workspace).filter(Workspace.guild_id == str(guild_id)).first()
        if w:
            return w
    return session.get(Workspace, 1)
