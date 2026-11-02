"""Verpakkingsrekenwerk dat voor elke supermarkt hetzelfde is.

Een ingredient ("500 g") en een verpakkingstekst ("2 x 250 g") worden naar gram,
milliliter of stuks omgerekend, waarna blijkt hoeveel verpakkingen nodig zijn. De
winkels leveren de verpakkingstekst aan; de berekening zelf is hier gedeeld.
"""

import math
import re
from typing import Any

from .i18n import tr

MAX_CART_QUANTITY = 99  # AH weigert een aantal buiten 1..99 met een 400


def normalize_quantity(value: Any) -> int:
    """Aantal verpakkingen voor het AH-winkelmandje, altijd binnen 1..99."""
    try:
        quantity = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 1
    return min(MAX_CART_QUANTITY, max(1, quantity))


_MASS_UNITS = {"g": 1, "gr": 1, "gram": 1, "kg": 1000, "kilo": 1000, "kilogram": 1000}
_VOLUME_UNITS = {
    "ml": 1, "milliliter": 1, "cl": 10, "centiliter": 10,
    "dl": 100, "deciliter": 100, "l": 1000, "liter": 1000,
}
_COUNT_UNITS = {"", "stuk", "stuks", "st"}
# Eenheden die een verpakking zijn: "2 potten witte bonen" is twee keer een product, hoe groot een pot ook is.
_CONTAINER_UNITS = {
    "pot", "potje", "potjes", "potten", "blik", "blikje", "blikjes", "blikken",
    "zak", "zakje", "zakjes", "zakken", "pak", "pakje", "pakjes", "pakken",
    "fles", "flesje", "flesjes", "flessen", "bak", "bakje", "bakjes", "bakken",
    "doos", "doosje", "doosjes", "dozen", "kuipje", "kuipjes", "beker", "bekers", "bekertje", "bekertjes",
    "tube", "tubes",
    # Engelse eenheden, zoals de recepten-parser ze ook kent
    "can", "cans", "jar", "jars", "tin", "tins", "bottle", "bottles", "bag", "bags",
    "pack", "packs", "package", "packages", "box", "boxes",
}
_PACK_SIZE_RE = re.compile(
    r"(?:(\d+)\s*[x\u00d7]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kilogram|kilo|kg|gram|gr|g|milliliter|centiliter|deciliter|liter|ml|cl|dl|l|stuks|stuk|st)\b",
    re.IGNORECASE,
)


def _measure(amount: float, unit: str) -> tuple[float, str] | None:
    """Reken een hoeveelheid om naar (waarde, soort): gram, milliliter of aantal stuks."""
    unit = unit.strip().lower().rstrip(".")
    if unit in _MASS_UNITS:
        return amount * _MASS_UNITS[unit], "mass"
    if unit in _VOLUME_UNITS:
        return amount * _VOLUME_UNITS[unit], "volume"
    if unit in _COUNT_UNITS:
        return amount, "count"
    return None


def parse_pack_size(unit_size: Any) -> tuple[float, str] | None:
    """Verpakkingsgrootte uit AH-tekst als "6 stuks", "1 kg", "4 x 125 g" of "per stuk"."""
    text = str(unit_size or "").strip().lower()
    if not text:
        return None
    match = _PACK_SIZE_RE.search(text)
    if match:
        multiplier = int(match.group(1) or 1)
        amount = float(match.group(2).replace(",", ".")) * multiplier
        return _measure(amount, match.group(3))
    if re.search(r"\bper stuk\b|\bstuk\b", text):
        return 1.0, "count"
    return None


def _pieces_in_pack(unit_size: Any) -> int | None:
    """Het aantal stuks in een verpakking als "3 × 67 gram"; None als de tekst geen "N ×" heeft."""
    match = _PACK_SIZE_RE.search(str(unit_size or "").strip().lower())
    pieces = int(match.group(1)) if match and match.group(1) else 0
    return pieces if pieces > 1 else None  # "1 x 2 liter" zegt niets over stuks


LARGE_PACK_COUNT = 10  # meer verpakkingen dan dit laten we de gebruiker controleren


def _counted(packs: int) -> tuple[int, str | None]:
    """Een berekend aantal verpakkingen, met twijfel als het er veel zijn."""
    packs = normalize_quantity(packs)
    if packs > LARGE_PACK_COUNT:
        return packs, tr("Groot aantal verpakkingen", "Large number of packs")
    return packs, None


def _assess_containers(needed: float, unit_text: str, unit_size: Any) -> tuple[int, str | None]:
    """Verpakkingen voor een ingrediënt in potten, blikken, zakken en dergelijke: één product per pot.

    `needed` is het aantal potten (groter dan 0), `unit_text` de eenheid zoals in het recept ("potten"),
    `unit_size` de verpakkingstekst van het product ("per stuk", "360 gram" of None).
    """
    pack = parse_pack_size(unit_size)
    if pack is not None and pack[1] == "count" and pack[0] > 0:
        packs, doubt = _counted(math.ceil(needed / pack[0] - 1e-9))
        if pack[0] > 1 and doubt is None:
            # "6 stuks" is een sixpack blikjes, maar net zo goed één doos met 6 eieren: delen, en laten nakijken.
            doubt = tr(f"{unit_text} bij {unit_size}: klopt het aantal?", f"{unit_text} with {unit_size}: is the number right?")
        return packs, doubt
    packs = normalize_quantity(math.ceil(needed - 1e-9))  # één product per pot; een halve pot is er ook één
    if pack is None:
        return packs, tr("Verpakkingsgrootte onbekend", "Pack size unknown")
    # Een gewicht of volume ("360 gram"): hoe groot een pot in het recept is, staat er niet. Dus wel kopen, maar laten nakijken.
    return packs, tr(
        f"{unit_text} past niet bij de verpakking ({unit_size})",
        f"{unit_text} doesn't match the pack ({unit_size})",
    )


def assess_packs(quantity: Any, unit: Any, unit_size: Any) -> tuple[int, str | None]:
    """Aantal verpakkingen voor een ingredient, plus de reden als de berekening onzeker is.

    "6 eieren" bij "10 stuks" is een doosje, "500 g" bij "250 g" zijn er twee. Wat niet
    te vergelijken valt (el, tl, teentje) of waar een gok volstaat ("2 uien" bij "1 kg")
    is een verpakking zonder twijfel. Twijfel is er bij een onbekende verpakkingsgrootte,
    gewicht of volume tegen stuks, of een groot aantal. "4 worstjes" bij "3 × 67 gram" rekent
    met de 3 stuks per pak. Een pot, blik, zak en dergelijke is één product per stuk; met een
    gewicht of een pak met meer stuks erbij is er twijfel.
    """
    unit_text = str(unit or "").strip()
    try:
        needed = float(quantity)
    except (TypeError, ValueError):
        needed = 1.0
    if not needed > 0 or math.isinf(needed):
        needed = 1.0

    if unit_text.lower().rstrip(".") in _CONTAINER_UNITS:
        return _assess_containers(needed, unit_text, unit_size)
    ingredient = _measure(needed, unit_text)
    if ingredient is None:
        return 1, None
    pack = parse_pack_size(unit_size)
    if pack is None:
        packs = normalize_quantity(needed) if ingredient[1] == "count" else 1
        return packs, tr("Verpakkingsgrootte onbekend", "Pack size unknown")
    if pack[1] != ingredient[1] or pack[0] <= 0:
        if ingredient[1] == "count":
            # "4 worstjes" bij "3 × 67 gram": de verpakking noemt het aantal stuks. Zonder "N ×" ("2 uien" bij
            # "1 kg") is één verpakking een goede gok.
            pieces = _pieces_in_pack(unit_size)
            if pieces:
                return _counted(math.ceil(ingredient[0] / pieces - 1e-9))
            return 1, None
        return 1, tr(
            f"{unit_text} past niet bij de verpakking ({unit_size})",
            f"{unit_text} doesn't match the pack ({unit_size})",
        )

    return _counted(math.ceil(ingredient[0] / pack[0] - 1e-9))


def packs_needed(quantity: Any, unit: Any, unit_size: Any) -> int:
    return assess_packs(quantity, unit, unit_size)[0]
