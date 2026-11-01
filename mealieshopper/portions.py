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

import sqlite3
from contextlib import closing
from os import environ
from typing import Any

from . import auth

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
