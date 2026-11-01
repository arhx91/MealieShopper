"""Porties per maaltijd: hoeveel personen een geplande maaltijd voedt, en met welke factor het mandje rekent.

Mealie bewaart per maaltijd in het weekmenu een `recipeScale` (standaard 1.0). De opbrengst van het recept is
`recipeServings`, of anders `recipeYieldQuantity`; 0 telt als niet ingevuld. Een maaltijd geldt als bewust
ingesteld als haar `recipeScale` niet 1.0 is (ook als iemand dat in Mealie zelf deed), of als er een markering in
`meal_portions_set` staat met hetzelfde recept. Zo'n markering is nodig voor een bewust gekozen 1.0: die is in Mealie
niet te onderscheiden van "nooit aangeraakt".

Niet ingesteld en een standaard ingesteld: de standaard geldt, alleen in MealieShopper (er gaat niets naar Mealie).
Niet ingesteld en geen standaard: de opbrengst van het recept, zoals vroeger.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from os import environ
from typing import Any

from . import auth, mealie
from .i18n import tr

log = logging.getLogger(__name__)

SETTING_DEFAULT = "DEFAULT_PORTIONS"
ENV_DEFAULT = "MEALIESHOPPER_DEFAULT_PORTIONS"
MIN_PORTIONS = 1
MAX_PORTIONS = 12
SCALE_TOLERANCE = 1e-6  # een recipeScale zo dicht bij 1.0 geldt als "niet ingesteld"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meal_portions_set (
  entry_id TEXT PRIMARY KEY,
  recipe_id TEXT NOT NULL,
  date TEXT NOT NULL,
  set_by TEXT NOT NULL,
  set_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = auth.connect()
    conn.executescript(SCHEMA)
    return conn


def _positive(value: Any) -> float | None:
    """Een getal groter dan 0 als float; anders None (ook voor True/False en tekst)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def recipe_yield(recipe: dict[str, Any] | None) -> float | None:
    """De opbrengst van een recept: `recipeServings`, anders `recipeYieldQuantity`; None als beide ontbreken of 0 zijn."""
    if not isinstance(recipe, dict):
        return None
    return _positive(recipe.get("recipeServings")) or _positive(recipe.get("recipeYieldQuantity"))


def _whole_portions(value: Any) -> int | None:
    """Een geheel aantal porties van MIN_PORTIONS tot en met MAX_PORTIONS uit een tekst; anders None."""
    try:
        number = int(str(value).strip())
    except ValueError:
        return None
    return number if MIN_PORTIONS <= number <= MAX_PORTIONS else None


def default_portions() -> int | None:
    """De standaard porties: de instelling in de app, anders de omgeving. Leeg of ongeldig betekent: uit."""
    setting = auth.get_setting(SETTING_DEFAULT).strip()
    value = setting or (environ.get(ENV_DEFAULT) or "").strip()
    return _whole_portions(value) if value else None


def mark(entry_id: str, recipe_id: str, date: str, set_by: str) -> None:
    """Onthoud dat iemand de porties van deze maaltijd bewust heeft ingesteld."""
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            INSERT INTO meal_portions_set (entry_id, recipe_id, date, set_by, set_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
              recipe_id=excluded.recipe_id, date=excluded.date, set_by=excluded.set_by, set_at=excluded.set_at
            """,
            (str(entry_id), str(recipe_id), str(date), set_by, auth.utcnow().isoformat()),
        )


def unmark(entry_id: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM meal_portions_set WHERE entry_id = ?", (str(entry_id),))


def is_marked(entry_id: str, recipe_id: str) -> bool:
    """Waar alleen als er een markering is voor deze maaltijd met hetzelfde recept.

    Mealie hergebruikt misschien het nummer van een verwijderde maaltijd (niet nagegaan); een oude markering hoort dan
    niet bij de nieuwe maaltijd. De datum telt niet mee: een verschoven maaltijd houdt haar porties.
    """
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT 1 FROM meal_portions_set WHERE entry_id = ? AND recipe_id = ?", (str(entry_id), str(recipe_id))
        ).fetchone()
    return row is not None


def _recipe_id(entry: dict[str, Any], recipe: dict[str, Any] | None) -> str:
    summary = entry.get("recipe") if isinstance(entry.get("recipe"), dict) else {}
    value = entry.get("recipeId") or summary.get("id") or (recipe or {}).get("id")
    return str(value or "")


def effective(entry: dict[str, Any], recipe: dict[str, Any] | None) -> dict[str, Any]:
    """De porties die voor deze maaltijd gelden en de factor voor de aantallen in het mandje.

    `source` is "set" (bewust ingesteld), "default" (de standaard), "recipe" (de opbrengst van het recept),
    "unknown" (een recept zonder opbrengst: niet schalen) of "unsupported" (een Mealie zonder `recipeScale`:
    niet schalen). De porties zijn afgerond op een geheel getal; de factor blijft een decimaal getal.
    """
    servings = recipe_yield(recipe)
    if "recipeScale" not in entry:
        return {"portions": None, "scale": 1.0, "source": "unsupported", "recipeServings": servings}
    if servings is None:
        return {"portions": None, "scale": 1.0, "source": "unknown", "recipeServings": None}

    scale = _positive(entry.get("recipeScale")) or 1.0
    if abs(scale - 1.0) >= SCALE_TOLERANCE or is_marked(str(entry.get("id") or ""), _recipe_id(entry, recipe)):
        return {"portions": round(scale * servings), "scale": scale, "source": "set", "recipeServings": servings}

    default = default_portions()
    if default is not None:
        return {"portions": default, "scale": default / servings, "source": "default", "recipeServings": servings}
    return {"portions": round(servings), "scale": 1.0, "source": "recipe", "recipeServings": servings}


def _writable(entry_id: str) -> tuple[dict[str, Any], dict[str, Any], float]:
    """De maaltijd, de samenvatting van haar recept en de opbrengst; een ValueError als de porties niet te zetten zijn.

    De opbrengst komt uit de samenvatting van het recept in de maaltijd (RecipeSummary heeft recipeServings en
    recipeYieldQuantity), dus er is geen tweede aanvraag voor het hele recept nodig.
    """
    entry = mealie.get_plan_entry(entry_id)
    recipe = entry.get("recipe") if isinstance(entry.get("recipe"), dict) else None
    if not recipe or not _recipe_id(entry, recipe):
        raise ValueError(tr("Deze maaltijd heeft geen recept.", "This meal has no recipe."))
    if "recipeScale" not in entry:
        raise ValueError(tr(
            "Deze versie van Mealie bewaart geen porties per maaltijd (recipeScale ontbreekt).",
            "This version of Mealie does not store portions per meal (recipeScale is missing).",
        ))
    servings = recipe_yield(recipe)
    if servings is None:
        raise ValueError(tr(
            "Het recept zegt niet voor hoeveel personen het is; vul dat eerst in Mealie in.",
            "The recipe does not say how many people it serves; fill that in in Mealie first.",
        ))
    return entry, recipe, servings


def _after_write(updated: Any, entry: dict[str, Any], recipe: dict[str, Any]) -> dict[str, Any]:
    """`effective` van de maaltijd zoals Mealie haar teruggaf; zonder recept in het antwoord de samenvatting van ervoor."""
    updated = updated if isinstance(updated, dict) else entry
    summary = updated.get("recipe") if isinstance(updated.get("recipe"), dict) else recipe
    return effective(updated, summary)


def set_portions(entry_id: str, portions: int, set_by: str) -> dict[str, Any]:
    """Zet de porties van één maaltijd: recipeScale = porties / opbrengst in Mealie, en daarna de markering.

    De markering komt pas na een gelukte PUT; een fout van Mealie (RuntimeError) laat alles zoals het was.
    """
    if isinstance(portions, bool) or not isinstance(portions, int) or not MIN_PORTIONS <= portions <= MAX_PORTIONS:
        raise ValueError(tr(
            f"Kies {MIN_PORTIONS} tot en met {MAX_PORTIONS} porties.",
            f"Choose {MIN_PORTIONS} to {MAX_PORTIONS} portions.",
        ))
    entry, recipe, servings = _writable(entry_id)
    updated = mealie.set_plan_entry_scale(entry_id, portions / servings)
    mark(str(entry_id), _recipe_id(entry, recipe), str(entry.get("date") or ""), set_by)
    log.info("Porties van maaltijd %s op %d gezet door %s", entry_id, portions, set_by)
    return _after_write(updated, entry, recipe)


def reset(entry_id: str, set_by: str) -> dict[str, Any]:
    """Terug naar de standaard: recipeScale = 1.0 in Mealie en de markering weg. Zonder standaard geldt het recept."""
    entry, recipe, _ = _writable(entry_id)
    updated = mealie.set_plan_entry_scale(entry_id, 1.0)
    unmark(str(entry_id))
    log.info("Porties van maaltijd %s teruggezet door %s", entry_id, set_by)
    return _after_write(updated, entry, recipe)
