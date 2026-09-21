#!/usr/bin/env python3
"""Genere static/destiny_board.json : l'arbre du Destiny Board (cote equipement).

3 archetypes (Guerrier / Chasseur / Mage), chacun avec ses lignes d'armes,
ses armures (tissu/cuir/plaques : casque, armure, bottes) et son off-main.
Farm/craft exclus. Noms FR nettoyes du palier, icone en T8 (tier max).
"""
import json
import os
import re
import sys
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE_DIR, "static", "destiny_board.json")

RAW = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
FORMATTED = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/formatted/items.json"

_QUALITY = re.compile(
    r"\s+(?:du grand maître|de l'apprenti|de l'adepte|de l'expert|du maître|"
    r"du sage|de l'ancien|de l'élu|du novice|du compagnon|du débutant)$", re.IGNORECASE)


def strip_quality(name):
    return _QUALITY.sub("", name).strip()


_QUALITY_EN = re.compile(
    r"^(?:Beginner's|Novice's|Journeyman's|Adept's|Expert's|Master's|"
    r"Grandmaster's|Elder's)\s+", re.IGNORECASE)


def strip_quality_en(name):
    return _QUALITY_EN.sub("", name).strip()


def unique_base(uniq):
    return re.sub(r"^T\d+_", "", uniq)


def tier_of(uniq):
    m = re.match(r"^T(\d+)_", uniq)
    return int(m.group(1)) if m else 0


def get_json(url):
    print("Telechargement:", url)
    return json.loads(urllib.request.urlopen(url, timeout=120).read().decode("utf-8"))


# lignes d'armes -> (archetype, label FR)
WEAPON_LINES = {
    "sword": ("warrior", "Épées", "Swords"), "axe": ("warrior", "Haches", "Axes"),
    "mace": ("warrior", "Masses", "Maces"), "hammer": ("warrior", "Marteaux", "Hammers"),
    "crossbow": ("warrior", "Arbalètes", "Crossbows"),
    "knuckles": ("warrior", "Gantelets de guerre", "War Gloves"),
    "bow": ("hunter", "Arcs", "Bows"), "spear": ("hunter", "Lances", "Spears"),
    "dagger": ("hunter", "Dagues", "Daggers"),
    "quarterstaff": ("hunter", "Bâtons de combat", "Quarterstaffs"),
    "naturestaff": ("hunter", "Bâtons de la nature", "Nature Staffs"),
    "firestaff": ("mage", "Bâtons de feu", "Fire Staffs"),
    "froststaff": ("mage", "Bâtons de givre", "Frost Staffs"),
    "arcanestaff": ("mage", "Bâtons arcaniques", "Arcane Staffs"),
    "cursestaff": ("mage", "Bâtons maudits", "Cursed Staffs"),
    "holystaff": ("mage", "Bâtons sacrés", "Holy Staffs"),
}
ARCH = {"warrior": "Guerrier", "hunter": "Chasseur", "mage": "Mage"}
MAT_ARCH = {"plate": "warrior", "leather": "hunter", "cloth": "mage"}
MAT_LABEL = {"plate": ("Plaques", "Plate"), "leather": ("Cuir", "Leather"),
             "cloth": ("Tissu", "Cloth")}
SLOT_LABEL = {"head": ("Casque", "Helmet"), "chest": ("Armure", "Armor"),
              "shoes": ("Bottes", "Shoes")}
OFF_ARCH = {"shieldtype": ("warrior", "Boucliers", "Shields"),
            "torchtype": ("hunter", "Torches", "Torches"),
            "booktype": ("mage", "Grimoires", "Tomes")}


def collect(entries):
    """uniq -> meilleure entree {tier, uniq} par item de base."""
    best = {}
    for uniq, _ in entries:
        if "@" in uniq:
            continue
        key = unique_base(uniq)
        t = tier_of(uniq)
        if key not in best or t > best[key][0]:
            best[key] = (t, uniq)
    return best


def main():
    raw = get_json(RAW)["items"]
    names = {}
    for e in get_json(FORMATTED):
        u = e.get("UniqueName")
        loc = e.get("LocalizedNames") or {}
        fr, en = loc.get("FR-FR") or "", loc.get("EN-US") or ""
        if u and (fr or en):
            names[u] = (strip_quality(fr or en), strip_quality_en(en or fr))

    def node(uniq):
        key = unique_base(uniq)
        fr, en = names.get(uniq, (key, key))
        return {"key": key, "label": fr, "label_en": en, "icon": uniq}

    # archetypes -> structure
    arch = {a: {"key": a, "label": ARCH[a], "weapons": [], "armor": [], "offhand": []}
            for a in ARCH}

    # --- armes ---
    lines = {k: [] for k in WEAPON_LINES}
    for w in raw["weapon"]:
        if w.get("@shopcategory") != "weapons":
            continue
        sub = w.get("@shopsubcategory1")
        if sub in lines:
            lines[sub].append((w["@uniquename"], sub))
    for sub, (a, label, label_en) in WEAPON_LINES.items():
        best = collect(lines[sub])
        nodes = [node(u) for _, u in sorted(best.values(), key=lambda x: -x[0])]
        nodes.sort(key=lambda n: n["label"])
        arch[a]["weapons"].append({"key": sub, "label": label, "label_en": label_en,
                                  "nodes": nodes})

    # --- armures (classees par nom unique : _PLATE_/_LEATHER_/_CLOTH_) ---
    armor = {(m, s): [] for m in MAT_ARCH for s in SLOT_LABEL}
    SLOTCAT = {"head": "head", "armors": "chest", "shoes": "shoes"}
    for e in raw["equipmentitem"]:
        sc = e.get("@shopcategory")
        slot = SLOTCAT.get(sc)
        if not slot:
            continue
        uniq = e["@uniquename"]
        mat = ("plate" if "_PLATE_" in uniq else "leather" if "_LEATHER_" in uniq
              else "cloth" if "_CLOTH_" in uniq else None)
        if mat:
            armor[(mat, slot)].append((uniq, mat))
    for (mat, slot), entries in armor.items():
        best = collect(entries)
        nodes = [node(u) for _, u in sorted(best.values(), key=lambda x: -x[0])]
        nodes.sort(key=lambda n: n["label"])
        if nodes:
            sfr, sen = SLOT_LABEL[slot]
            mfr, men = MAT_LABEL[mat]
            arch[MAT_ARCH[mat]]["armor"].append(
                {"key": f"{mat}_{slot}", "label": f"{sfr} ({mfr})",
                 "label_en": f"{sen} ({men})", "nodes": nodes})

    # --- off-mains ---
    offs = {k: [] for k in OFF_ARCH}
    for e in raw["equipmentitem"]:
        if e.get("@shopcategory") != "offhands":
            continue
        sub = e.get("@shopsubcategory1")
        if sub in offs:
            offs[sub].append((e["@uniquename"], sub))
    for sub, (a, label, label_en) in OFF_ARCH.items():
        best = collect(offs[sub])
        nodes = [node(u) for _, u in sorted(best.values(), key=lambda x: -x[0])]
        nodes.sort(key=lambda n: n["label"])
        arch[a]["offhand"].append({"key": sub, "label": label, "label_en": label_en,
                                  "nodes": nodes})

    out = {"archetypes": [arch["warrior"], arch["hunter"], arch["mage"]]}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    total = sum(len(g["nodes"]) for a in out["archetypes"]
               for grp in ("weapons", "armor", "offhand") for g in a[grp])
    print(f"OK -> {OUT} ({total} noeuds)")


if __name__ == "__main__":
    sys.exit(main())
