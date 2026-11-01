import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, ingredients, mealie, portions, week_ingredients  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402


def line(food=None, unit=None, quantity=None, display=""):
    return {
        "food": {"name": food} if food else None,
        "unit": {"name": unit} if unit else None,
        "quantity": quantity,
        "display": display,
    }


def entry(*lines):
    return {"title": "", "recipeDetail": {"recipeIngredient": list(lines)}}


class BasketLinesTest(unittest.TestCase):
    def test_same_food_and_unit_is_summed(self):
        lines = week_ingredients.basket_lines([
            entry(line("Ui", "stuks", 2, "2 stuks Ui")),
            entry(line("Ui", "stuks", 1, "1 stuks Ui")),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["quantity"], 3)
        self.assertEqual(lines[0]["display"], "3 stuks Ui")
        self.assertEqual(lines[0], {"query": "Ui", "display": "3 stuks Ui", "quantity": 3, "unit": "stuks"})

    def test_same_food_other_unit_stays_separate(self):
        lines = week_ingredients.basket_lines([
            entry(line("ui", "stuks", 2, "2 stuks ui")),
            entry(line("ui", "g", 200, "200 g ui")),
        ])
        self.assertEqual(len(lines), 2)

    def test_food_name_match_ignores_case(self):
        lines = week_ingredients.basket_lines([
            entry(line("Ui", "stuks", 2, "2 stuks Ui")),
            entry(line("ui", "stuks", 1, "1 stuks ui")),
        ])
        self.assertEqual(len(lines), 1)

    def test_line_without_quantity_is_kept_once(self):
        lines = week_ingredients.basket_lines([
            entry(line("zout", display="zout")),
            entry(line("zout", display="zout")),
        ])
        self.assertEqual(len(lines), 1)
        self.assertIsNone(lines[0]["quantity"])
        self.assertEqual(lines[0]["display"], "zout")

    def test_line_without_food_uses_display_as_key(self):
        lines = week_ingredients.basket_lines([
            entry(line(quantity=1, display="Een snufje peper")),
            entry(line(quantity=1, display="een snufje peper")),
        ])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["query"], "Een snufje peper")
        # Het aantal is Mealie's standaard, geen echte hoeveelheid: niet optellen.
        self.assertEqual(lines[0]["quantity"], 1)
        self.assertEqual(lines[0]["display"], "Een snufje peper")

    def test_line_without_food_never_merges_with_a_food_of_the_same_name(self):
        # Een tekstregel "zout" (Mealie's standaard 1) en het ingrediënt zout (2, zonder eenheid) zijn verschillende
        # regels; samen gaven ze 3 of 2, afhankelijk van de volgorde, en een tekst die niet bij het aantal paste.
        text, food = line(quantity=1, display="zout"), line("zout", quantity=2, display="2 zout")
        for order in ((text, food), (food, text)):
            lines = week_ingredients.basket_lines([entry(*order)])
            self.assertEqual(
                sorted((l["query"], l["display"], l["quantity"]) for l in lines),
                [("zout", "2 zout", 2), ("zout", "zout", 1)],
                order,
            )

    def test_first_line_without_quantity_takes_the_later_quantity(self):
        # De eerste regel heeft geen aantal, een latere wel: de som is het bekende aantal en de tekst volgt die som.
        lines = week_ingredients.basket_lines([
            entry(line("ui", "stuks", display="ui")),
            entry(line("ui", "stuks", 2, "2 stuks ui")),
        ])
        self.assertEqual(lines, [{"query": "ui", "display": "2 stuks ui", "quantity": 2, "unit": "stuks"}])

    def test_entries_without_recipe_are_skipped(self):
        self.assertEqual(week_ingredients.basket_lines([{"title": "Uit eten"}]), [])

    def test_a_single_line_keeps_its_original_display(self):
        lines = week_ingredients.basket_lines([
            entry(line("tomaat", "stuks", 4, "4 stuks tomaat (rijp)")),
        ])
        self.assertEqual(lines[0]["display"], "4 stuks tomaat (rijp)")


def scaled(scale, *lines, recipe=None):
    """Een maaltijd met de porties zoals /api/mealplan die meegeeft."""
    return {"title": "", "recipeDetail": recipe or {"recipeIngredient": list(lines)}, "portions": {"scale": scale}}


def by_scale(entry):
    return entry["portions"]["scale"]


class ScaledLinesTest(unittest.TestCase):
    def test_default_halves_a_four_person_recipe(self):
        lines = week_ingredients.basket_lines([scaled(0.5, line("ui", "stuks", 4, "4 stuks ui"))], scale_of=by_scale)
        self.assertEqual(lines[0]["quantity"], 2)

    def test_lines_without_food_are_not_scaled(self):
        # Review Focus 1: het aantal van een regel die Mealie niet analyseerde is Mealie's standaard, geen hoeveelheid.
        salt = {"food": None, "unit": None, "quantity": 1, "note": "1 snufje zout", "display": "1 snufje zout"}
        lines = week_ingredients.basket_lines([scaled(0.5, salt)], scale_of=by_scale)
        self.assertEqual(lines[0]["quantity"], 1)
        self.assertEqual(lines[0]["display"], "1 snufje zout")

    def test_same_recipe_twice_is_scaled_per_entry(self):
        # Review Focus 2: get_meal_plan_with_recipes deelt één recipeDetail per recept over de maaltijden.
        shared = {"recipeIngredient": [line("ui", "stuks", 4, "4 stuks ui")]}
        entries = [scaled(0.5, recipe=shared), scaled(1.0, recipe=shared)]
        lines = week_ingredients.basket_lines(entries, scale_of=by_scale)
        self.assertEqual(lines[0]["quantity"], 6)
        self.assertEqual(shared["recipeIngredient"][0]["quantity"], 4, "het gedeelde recept blijft ongewijzigd")
        self.assertEqual(entries[0]["recipeDetail"]["recipeIngredient"][0]["display"], "4 stuks ui")

    def test_uneven_scale_is_formatted(self):
        with mock.patch.object(ingredients, "current_lang", return_value="nl"):
            eggs = week_ingredients.basket_lines([scaled(2 / 3, line("ei", "stuks", 3, "3 stuks ei"))], scale_of=by_scale)
            cheese = week_ingredients.basket_lines([scaled(2 / 3, line("kaas", "g", 100, "100 g kaas"))], scale_of=by_scale)
        self.assertEqual(eggs[0]["display"], "2 stuks ei")
        self.assertEqual(cheese[0]["display"], "66,67 g kaas")

    def test_scale_one_everywhere_is_todays_basket(self):
        lines = [
            line("Ui", "stuks", 2, "2 stuks Ui"), line("ui", "g", 200, "200 g ui"),
            line(quantity=1, display="Een snufje peper"), line("zout", display="zout"),
        ]
        plain = [entry(*lines), entry(line("Ui", "stuks", 1, "1 stuks Ui"))]
        with_portions = [scaled(1.0, *lines), scaled(1.0, line("Ui", "stuks", 1, "1 stuks Ui"))]
        self.assertEqual(week_ingredients.basket_lines(with_portions, scale_of=by_scale), week_ingredients.basket_lines(plain))


class RouteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._INITIALIZED = False
        self.client = create_app().test_client()

    def test_route_returns_summed_lines(self):
        entries = [entry(line("Ui", "stuks", 2, "2 stuks Ui")), entry(line("Ui", "stuks", 1, "1 stuks Ui"))]
        with mock.patch.object(mealie, "get_meal_plan_with_recipes") as get:
            response = self.client.post("/api/mealplan/ingredients", json={"entries": entries})
        self.assertEqual(response.status_code, 200)
        get.assert_not_called()  # de lijst komt uit de getoonde maaltijden, niet opnieuw uit Mealie
        self.assertEqual(
            response.get_json(),
            {"items": [{"query": "Ui", "display": "3 stuks Ui", "quantity": 3, "unit": "stuks"}]},
        )

    def test_route_needs_a_list_of_entries(self):
        for body in ({}, {"entries": None}, {"entries": "geen lijst"}, {"entries": {"title": "x"}}):
            response = self.client.post("/api/mealplan/ingredients", json=body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn("error", response.get_json())
        self.assertEqual(self.client.post("/api/mealplan/ingredients", data="geen json").status_code, 400)

    def test_route_scales_by_the_portions_of_each_entry(self):
        entries = [
            scaled(0.5, line("ui", "stuks", 4, "4 stuks ui")),
            entry(line("ui", "stuks", 4, "4 stuks ui")),  # zonder "portions" (oude pagina): ongeschaald
            {**entry(line("ui", "stuks", 4, "4 stuks ui")), "portions": None},
        ]
        response = self.client.post("/api/mealplan/ingredients", json={"entries": entries})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["items"][0]["quantity"], 10)

    def test_week_entries_carry_portions(self):
        plan = [
            {"id": 1, "date": "2026-11-03", "recipeId": "r1", "recipeScale": 1.0, "recipe": {"id": "r1", "slug": "pasta"}},
            {"id": 2, "date": "2026-11-04", "recipeId": "r1", "recipeScale": 0.5, "recipe": {"id": "r1", "slug": "pasta"}},
            {"id": 3, "date": "2026-11-05", "recipeId": None, "recipeScale": 1.0, "title": "Uit eten"},
        ]
        recipe = {"id": "r1", "slug": "pasta", "recipeServings": 4, "recipeIngredient": []}
        auth.set_setting(portions.SETTING_DEFAULT, "2")
        with mock.patch.object(mealie, "get_meal_plan", return_value=plan), \
                mock.patch.object(mealie, "get_recipe", return_value=recipe), \
                mock.patch.object(mealie, "_image_path", return_value=None):
            week = mealie.get_meal_plan_with_recipes("2026-11-03", "2026-11-09")
        self.assertEqual(len(week), 3)
        for item in week:
            self.assertIsInstance(item["portions"], dict, item["id"])
            self.assertEqual(set(item["portions"]), {"portions", "scale", "source", "recipeServings"})
        self.assertEqual([item["portions"]["source"] for item in week], ["default", "set", "unknown"])
        self.assertEqual(week[0]["portions"]["scale"], 0.5)

    def test_text_only_entry_goes_through_the_week_and_the_basket(self):
        # De weekplanner schrijft maaltijden zonder recept ("Restjes: ..."); Mealie geeft dan "recipe": null.
        plan = [
            {"id": 1, "date": "2026-11-03", "recipeId": "r1", "recipeScale": 1.0, "recipe": {"id": "r1", "slug": "chili"}},
            {"id": 2, "date": "2026-11-04", "recipeId": None, "recipeScale": 1.0, "recipe": None,
             "title": "Restjes: chili (van maandag)"},
        ]
        recipe = {"id": "r1", "slug": "chili", "recipeServings": 4,
                  "recipeIngredient": [line("ui", "stuks", 4, "4 stuks ui")]}
        auth.set_setting(portions.SETTING_DEFAULT, "2")
        with mock.patch.object(mealie, "get_meal_plan", return_value=plan), \
                mock.patch.object(mealie, "get_recipe", return_value=recipe) as get_recipe, \
                mock.patch.object(mealie, "_image_path", return_value=None):
            week = mealie.get_meal_plan_with_recipes("2026-11-03", "2026-11-09")
        get_recipe.assert_called_once_with("chili")
        leftovers = week[1]
        self.assertIsNone(leftovers["recipeDetail"])
        self.assertEqual(leftovers["title"], "Restjes: chili (van maandag)")
        self.assertEqual(leftovers["portions"], {"portions": None, "scale": 1.0, "source": "unknown", "recipeServings": None})

        expected = [{"query": "ui", "display": "2 stuks ui", "quantity": 2.0, "unit": "stuks"}]
        with mock.patch.object(ingredients, "current_lang", return_value="nl"):
            self.assertEqual(week_ingredients.basket_lines(week, scale_of=lambda e: e["portions"]["scale"]), expected)
        posted = self.client.post("/api/mealplan/ingredients", json={"entries": week})
        self.assertEqual(posted.status_code, 200, posted.get_json())
        self.assertEqual(posted.get_json()["items"], expected)

    def test_route_buys_junk_portions_unscaled(self):
        junk = ["x", 5, [0.5], {"scale": -1}, {"scale": 0}, {"scale": "2"}, {"scale": True}, {"scale": None}, {}]
        entries = [{**entry(line("ui", "stuks", 4, "4 stuks ui")), "portions": value} for value in junk]
        response = self.client.post("/api/mealplan/ingredients", json={"entries": entries})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()["items"][0]["quantity"], 4 * len(junk))
        # NaN en oneindig kunnen niet als gewone JSON mee; die zet Flask's parser om naar float.
        for text in ("NaN", "Infinity", "-Infinity"):
            body = '{"entries": [{"recipeDetail": {"recipeIngredient": [{"food": {"name": "ui"}, "quantity": 4}]}, ' \
                   '"portions": {"scale": %s}}]}' % text
            response = self.client.post("/api/mealplan/ingredients", data=body, content_type="application/json")
            self.assertEqual(response.status_code, 200, text)
            self.assertEqual(response.get_json()["items"][0]["quantity"], 4, text)


if __name__ == "__main__":
    unittest.main()
