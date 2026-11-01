import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, mealie, week_ingredients  # noqa: E402
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

    def test_entries_without_recipe_are_skipped(self):
        self.assertEqual(week_ingredients.basket_lines([{"title": "Uit eten"}]), [])

    def test_a_single_line_keeps_its_original_display(self):
        lines = week_ingredients.basket_lines([
            entry(line("tomaat", "stuks", 4, "4 stuks tomaat (rijp)")),
        ])
        self.assertEqual(lines[0]["display"], "4 stuks tomaat (rijp)")


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
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", return_value=entries) as get:
            response = self.client.get("/api/mealplan/ingredients?start=2026-11-02&end=2026-11-08")
        self.assertEqual(response.status_code, 200)
        get.assert_called_once_with("2026-11-02", "2026-11-08")
        self.assertEqual(
            response.get_json(),
            {"items": [{"query": "Ui", "display": "3 stuks Ui", "quantity": 3, "unit": "stuks"}]},
        )

    def test_route_needs_both_dates(self):
        with mock.patch.object(mealie, "get_meal_plan_with_recipes") as get:
            response = self.client.get("/api/mealplan/ingredients?start=2026-11-02")
        self.assertEqual(response.status_code, 400)
        get.assert_not_called()

    def test_route_reports_a_mealie_error_as_424(self):
        with mock.patch.object(mealie, "get_meal_plan_with_recipes", side_effect=RuntimeError("Mealie weg")), \
                self.assertLogs("mealieshopper.app", "ERROR"):
            response = self.client.get("/api/mealplan/ingredients?start=2026-11-02&end=2026-11-08")
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.get_json()["error"], "Mealie weg")


if __name__ == "__main__":
    unittest.main()
