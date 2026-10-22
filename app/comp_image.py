"""Genere une image PNG d'une composition (style albioncompo)."""
import json
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_DIR = os.path.join(BASE_DIR, "static", "icons")
OUT_DIR = os.path.join(BASE_DIR, "data", "images")  # hors /static : servi seulement via les routes protegees
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# couleurs (fond sombre facon albioncompo)
BG = (13, 19, 30)
BOX = (30, 38, 52)
BOX_LINE = (48, 59, 76)
TXT = (230, 237, 243)
MUTED = (139, 151, 163)
GOLD = (212, 162, 74)
CAT_COLORS = {
    "tank": (212, 162, 74), "heal": (95, 191, 127), "dps_melee": (232, 106, 90),
    "dps_range": (74, 134, 212), "support": (176, 130, 220), "": (139, 151, 163),
}

GUILD_NAME = "DWAlbion builder"

CATEGORIES = [("tank", "Tank"), ("dps_melee", "DPS mêlée"), ("dps_range", "DPS distance"),
              ("support", "Support"), ("heal", "Heal")]
GEAR_ORDER = ("weapon", "offhand", "head", "chest", "shoes", "cape", "food", "potion")

ICON = 64          # taille d'une icone
PAD = 8            # espace entre cases
ITEMS_PER_ROW = 8
COL_W = ITEMS_PER_ROW * (ICON + PAD)
MARGIN = 40

_IDX = None


def _index():
    """nom d'item (minuscule) -> uniquename, pour retrouver l'icone."""
    global _IDX
    if _IDX is None:
        with open(os.path.join(BASE_DIR, "static", "albion_items.json"), encoding="utf-8") as f:
            data = json.load(f)
        _IDX = {it["name"].lower(): it["unique"] for items in data.values() for it in items}
    return _IDX


def _icon(name: str, size: int = ICON):
    """Icone PIL d'un item (telechargee puis mise en cache sur disque)."""
    uniq = _index().get((name or "").lower())
    if not uniq:
        return None
    os.makedirs(ICON_DIR, exist_ok=True)
    path = os.path.join(ICON_DIR, f"{uniq}.png")
    if not os.path.exists(path):
        url = f"https://render.albiononline.com/v1/item/{uniq}.png?quality=1&size=128"
        try:
            with urllib.request.urlopen(url, timeout=20) as r, open(path, "wb") as f:
                f.write(r.read())
        except Exception:
            return None
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def sections_from_comp(comp) -> list:
    """Construit les sections (par categorie) depuis une composition.
    A appeler tant que la session SQLAlchemy est ouverte."""
    from sqlalchemy.orm import object_session
    from .db import Build
    sess = object_session(comp)
    by_cat = {k: [] for k, _ in CATEGORIES}
    other = []
    for sl in comp.slots:
        sw = dict(sl.swaps or {})
        # fallback : recupere les swaps du build si le slot n'en a pas (vieilles compos)
        if sess is not None and getattr(sl, "build_id", None):
            b = sess.get(Build, sl.build_id)
            if b and b.swaps:
                for k, v in b.swaps.items():
                    sw.setdefault(k, v)
        row = [(getattr(sl, k, ""), sw.get(k)) for k in GEAR_ORDER if getattr(sl, k, "")]
        if not row:
            continue
        (by_cat[sl.category] if sl.category in by_cat else other).append(row)
    secs = [{"key": k, "label": lbl, "rows": by_cat[k]} for k, lbl in CATEGORIES if by_cat[k]]
    if other:
        secs.append({"key": "", "label": "Autres rôles", "rows": other})
    return secs


def render_comp(title: str, subtitle: str, sections: list, out_name: str) -> str:
    """sections = [{'label':..., 'key':..., 'rows':[[nom_item, ...], ...]}, ...]
    Retourne le chemin du PNG genere."""
    f_title = _font(FONT_BOLD, 38)
    f_sub = _font(FONT_REG, 18)
    f_cat = _font(FONT_BOLD, 20)
    f_small = _font(FONT_REG, 13)

    sections = [s for s in sections if s["rows"]]
    # repartit les sections en colonnes (max 3) en equilibrant le nombre de lignes
    ncols = min(3, max(1, len(sections)))
    cols = [[] for _ in range(ncols)]
    heights = [0] * ncols
    for s in sorted(sections, key=lambda x: -len(x["rows"])):
        i = heights.index(min(heights))
        cols[i].append(s)
        heights[i] += len(s["rows"]) * (ICON + PAD) + 60

    header_h = 130
    body_h = max(heights) if heights else 100
    width = MARGIN * 2 + ncols * COL_W + (ncols - 1) * 30
    height = header_h + body_h + MARGIN

    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)

    # entete
    d.text((width // 2, 36), (title or "Composition").upper(), font=f_title,
           fill=TXT, anchor="mm")
    if subtitle:
        d.text((width // 2, 74), subtitle, font=f_sub, fill=GOLD, anchor="mm")
    d.line([(width // 2 - 90, 96), (width // 2 + 90, 96)], fill=GOLD, width=2)

    # colonnes
    for ci, col in enumerate(cols):
        x0 = MARGIN + ci * (COL_W + 30)
        y = header_h
        for sec in col:
            color = CAT_COLORS.get(sec.get("key", ""), MUTED)
            d.text((x0, y), sec["label"].upper(), font=f_cat, fill=color)
            d.line([(x0, y + 26), (x0 + COL_W - 20, y + 26)], fill=BOX_LINE, width=2)
            y += 40
            for row in sec["rows"]:
                x = x0
                for name, swap in row:
                    d.rounded_rectangle([x, y, x + ICON, y + ICON], radius=8,
                                       fill=BOX, outline=BOX_LINE)
                    ic = _icon(name)
                    if ic:
                        img.paste(ic, (x, y), ic)
                    # item de swap : petit carre en haut a droite (sans contour)
                    if swap:
                        sz = 26
                        sw_ic = _icon(swap, size=sz)
                        if sw_ic:
                            sx, sy = x + ICON - sz, y
                            d.rounded_rectangle([sx - 1, sy - 1, sx + sz, sy + sz],
                                               radius=4, fill=BG)
                            img.paste(sw_ic, (sx, sy), sw_ic)
                    x += ICON + PAD
                y += ICON + PAD
            y += 20

    d.text((width - MARGIN, height - 18), "guild.dwa.gold", font=f_small, fill=MUTED, anchor="rm")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, out_name)
    img.save(path, "PNG", optimize=True)
    return path
