#!/usr/bin/env python3
"""Genere static/albion_items.json a partir des dumps officiels ao-bin-dumps.

- Equipement (arme, off-hand, tete, torse, bottes, cape) : on ignore le tier,
  on garde le **T8** (tier max dispo), jamais d'enchantement -> une entree par item.
- Food & potion : on garde chaque **tier** (prefixe "T4 ...") mais pas d'enchantement.
"""
import json
import os
import re
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE_DIR, "static", "albion_items.json")

SOURCES = [
    "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json",
    "https://raw.githubusercontent.com/broderickhyman/ao-bin-dumps/master/formatted/items.json",
]
RAW_SOURCE = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"

# mots-cles de recherche par ligne d'arme (FR + EN) : chercher "sword" ou "epee"
# doit sortir toutes les epees
LINE_TERMS = {
    "sword": "épées épée sword swords", "axe": "haches hache axe axes",
    "mace": "masses masse mace maces", "hammer": "marteaux marteau hammer hammers",
    "crossbow": "arbaletes arbalète arbalete crossbow crossbows",
    "knuckles": "gantelets gantelet war gloves knuckles poings",
    "bow": "arcs arc bow bows", "spear": "lances lance spear spears",
    "dagger": "dagues dague dagger daggers",
    "quarterstaff": "bâtons de combat baton quarterstaff quarterstaffs",
    "naturestaff": "bâtons de la nature nature staff naturestaff",
    "firestaff": "bâtons de feu fire staff firestaff",
    "froststaff": "bâtons de givre frost staff froststaff",
    "arcanestaff": "bâtons arcaniques arcane staff arcanestaff",
    "cursestaff": "bâtons maudits cursed staff cursestaff curse",
    "holystaff": "bâtons sacrés holy staff holystaff",
}
OFF_TERMS = {"shieldtype": "boucliers bouclier shield shields",
             "torchtype": "torches torche torch",
             "booktype": "grimoires grimoire tome tomes book books"}
MAT_TERMS = {"PLATE": "plaques plate", "LEATHER": "cuir leather", "CLOTH": "tissu cloth"}
SLOT_TERMS = {"head": "casque helmet head", "chest": "armure torse armor chest",
              "shoes": "bottes chaussures shoes boots"}

CATEGORIES = {
    "weapon":  re.compile(r"^T\d+_(2H|MAIN)_(?!TOOL)"),
    "offhand": re.compile(r"^T\d+_OFF_"),
    "head":    re.compile(r"^T\d+_HEAD_"),
    "chest":   re.compile(r"^T\d+_ARMOR_"),
    "shoes":   re.compile(r"^T\d+_SHOES_"),
    "cape":    re.compile(r"^T\d+_CAPE"),
    "food":    re.compile(r"^T\d+_MEAL_"),
    "potion":  re.compile(r"^T\d+_POTION_"),
}
EQUIP = {"weapon", "offhand", "head", "chest", "shoes", "cape"}  # -> tier max (T8)
TIERED = {"food", "potion"}                                      # -> garde le tier

# capes a exclure : emblemes de craft (_BP), capes decoratives (vanity), banniere d'arene
CAPE_EXCLUDE = re.compile(r"_BP$|_ARENA_|_CAPE_(PLATE|LEATHER|CLOTH)_")


def fetch() -> list:
    last = None
    for url in SOURCES:
        try:
            print("Telechargement:", url)
            with urllib.request.urlopen(url, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            last = e
            print("  echec:", e)
    raise SystemExit(f"Impossible de telecharger les items: {last}")


def localized(entry: dict) -> str:
    names = entry.get("LocalizedNames") or {}
    return names.get("FR-FR") or names.get("EN-US") or ""


# palier en anglais : prefixe ("Elder's Kingmaker")
_QUALITY_EN = re.compile(
    r"^(?:Beginner's|Novice's|Journeyman's|Adept's|Expert's|Master's|"
    r"Grandmaster's|Elder's)\s+", re.IGNORECASE)


def strip_quality_en(name: str) -> str:
    return _QUALITY_EN.sub("", name).strip()


def both_names(entry: dict):
    """(nom FR nettoye, nom EN nettoye) — retombe sur l'autre langue si absent."""
    loc = entry.get("LocalizedNames") or {}
    fr, en = loc.get("FR-FR") or "", loc.get("EN-US") or ""
    fr_c = strip_quality(fr or en)
    en_c = strip_quality_en(en or fr)
    return fr_c, en_c


def tier_of(uniq: str) -> int:
    m = re.match(r"^T(\d+)_", uniq)
    return int(m.group(1)) if m else 0


def unique_base(uniq: str) -> str:
    """Nom unique sans le prefixe de tier : T8_2H_CLAYMORE -> 2H_CLAYMORE."""
    return re.sub(r"^T\d+_", "", uniq)


# suffixes de palier (FR) a retirer du nom d'equipement (le plus long d'abord)
_QUALITY = re.compile(
    r"\s+(?:du grand maître|de l'apprenti|de l'adepte|de l'expert|du maître|"
    r"du sage|de l'ancien|de l'élu|du novice|du compagnon|du débutant)$",
    re.IGNORECASE)


def strip_quality(name: str) -> str:
    return _QUALITY.sub("", name).strip()


def raw_categories() -> dict:
    """uniquename -> (shopcategory, shopsubcategory1) depuis le dump brut."""
    print("Telechargement (categories):", RAW_SOURCE)
    raw = json.loads(urllib.request.urlopen(RAW_SOURCE, timeout=120).read().decode("utf-8"))
    items = raw["items"]
    out = {}
    for group in ("weapon", "equipmentitem"):
        for e in items.get(group, []):
            out[e.get("@uniquename", "")] = (e.get("@shopcategory"), e.get("@shopsubcategory1"))
    return out


def search_terms(uniq: str, cat: str, cats: dict) -> str:
    """Mots-cles de categorie (FR+EN) pour qu'une recherche 'sword'/'epee' marche."""
    sc, sub = cats.get(uniq, (None, None))
    parts = []
    if cat == "weapon":
        parts.append(LINE_TERMS.get(sub, sub or ""))
    elif cat == "offhand":
        parts.append(OFF_TERMS.get(sub, sub or ""))
    elif cat in ("head", "chest", "shoes"):
        parts.append(SLOT_TERMS.get(cat, ""))
        for mat, terms in MAT_TERMS.items():
            if f"_{mat}_" in uniq:
                parts.append(terms)
                break
    elif cat == "cape":
        parts.append("cape capes")
    elif cat == "food":
        parts.append("nourriture food meal repas")
    elif cat == "potion":
        parts.append("potion potions")
    return " ".join(p for p in parts if p)


def main():
    data = fetch()
    cats = raw_categories()
    best = {c: {} for c in EQUIP}      # cat -> unique_base -> (tier, entry)
    tiered = {c: {} for c in TIERED}   # cat -> namelower -> entry
    for entry in data:
        uniq = entry.get("UniqueName", "")
        if not uniq or "@" in uniq:    # jamais d'enchantement
            continue
        name_fr, name_en = both_names(entry)
        if not name_fr:
            continue
        for cat, rx in CATEGORIES.items():
            if not rx.match(uniq):
                continue
            if cat == "cape" and CAPE_EXCLUDE.search(uniq):
                break  # emblemes de craft / capes decoratives : ignore
            t = tier_of(uniq)
            if cat in EQUIP:
                # regroupe par item reel, garde le tier max (T8) + nom sans palier
                key = unique_base(uniq)
                cur = best[cat].get(key)
                if cur is None or t > cur[0]:
                    best[cat][key] = (t, {"unique": uniq, "name": name_fr, "name_en": name_en})
            else:  # food / potion : garde le tier
                disp = f"T{t} {name_fr}" if t else name_fr
                disp_en = f"T{t} {name_en}" if t else name_en
                tiered[cat].setdefault(disp.lower(), {"unique": uniq, "name": disp, "name_en": disp_en})
            break

    out = {}
    for cat in CATEGORIES:
        if cat in EQUIP:
            # dedoublonne aussi par nom affiche (au cas ou)
            seen, items = set(), []
            for _, e in sorted(best[cat].values(), key=lambda x: -x[0]):
                k = e["name"].lower()
                if k not in seen:
                    seen.add(k)
                    items.append(e)
        else:
            items = list(tiered[cat].values())
        items.sort(key=lambda x: x["name"])
        for e in items:  # mots-cles de categorie pour la recherche
            e["terms"] = search_terms(e["unique"], cat, cats)
        out[cat] = items

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    total = sum(len(v) for v in out.values())
    print(f"OK -> {OUT} ({total} items :", {c: len(v) for c, v in out.items()}, ")")


if __name__ == "__main__":
    sys.exit(main())
