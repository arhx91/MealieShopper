"""De boodschappenlijst van een week: alle ingrediënten van de geplande recepten, gedeelde ingrediënten opgeteld.

Twee regels met hetzelfde ingrediënt en dezelfde eenheid worden één regel met de som van de aantallen
("2 stuks ui" en "1 stuks ui" wordt "3 stuks ui"). Een andere eenheid blijft een eigen regel. Een regel
zonder ingrediënt (die Mealie niet analyseerde) heeft als aantal alleen Mealie's standaard; die tellen we
niet op maar houden we één keer, met de tekst als sleutel. Die sleutels staan los van die van de ingrediënten:
een tekstregel "zout" valt nooit samen met het ingrediënt zout.
"""
from __future__ import annotations

from typing import Any, Callable

from .ingredients import format_quantity


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("abbreviation") or "").strip()
    return ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def basket_lines(
    entries: list[dict[str, Any]], scale_of: Callable[[dict[str, Any]], float] = lambda entry: 1.0
) -> list[dict[str, Any]]:
    """Regels in de vorm die `/api/<winkel>/cart/preview` verwacht: query, display, quantity en unit.

    `scale_of(entry)` geeft de factor voor de aantallen van een maaltijd; alleen regels met een ingrediënt
    worden geschaald. De volgorde is die van de eerste keer dat een regel voorkomt.
    """
    lines: dict[tuple[str, ...], dict[str, Any]] = {}
    for entry in entries or []:
        recipe = entry.get("recipeDetail") if isinstance(entry, dict) else None
        if not isinstance(recipe, dict):
            continue
        scale = None
        for ingredient in recipe.get("recipeIngredient") or []:
            if not isinstance(ingredient, dict):
                continue
            food = _name(ingredient.get("food"))
            unit = _name(ingredient.get("unit"))
            display = str(ingredient.get("display") or "").strip()
            if not food and not display:
                continue
            # Eigen sleutelruimtes: een tekstregel en een ingrediënt met dezelfde naam blijven twee regels.
            key = ("food", food.lower(), unit.lower()) if food else ("text", display.lower())
            quantity = _number(ingredient.get("quantity"))
            if food and quantity is not None:
                if scale is None:
                    scale = scale_of(entry)
                quantity *= scale

            line = lines.get(key)
            if line is None:
                lines[key] = {
                    "query": food or display,
                    "display": display or food,
                    "quantity": quantity,
                    "unit": unit or None,
                    "_original": ingredient.get("quantity"),
                    "_food": food,
                }
            elif food and quantity is not None:
                line["quantity"] = quantity if line["quantity"] is None else line["quantity"] + quantity

    result = []
    for line in lines.values():
        original, food = line.pop("_original"), line.pop("_food")
        if food and line["quantity"] is not None and line["quantity"] != original:
            line["display"] = " ".join(part for part in (format_quantity(line["quantity"]), line["unit"], food) if part)
        result.append(line)
    return result
