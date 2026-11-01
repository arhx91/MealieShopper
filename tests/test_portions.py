import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

import requests  # noqa: E402

from mealieshopper import auth, mealie, portions  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ENV_DEFAULT = "MEALIESHOPPER_DEFAULT_PORTIONS"
URL = "https://mealie.example.nl"


def response(status=200, body=None, text=""):
    r = mock.Mock(status_code=status, ok=200 <= status < 300, text=text)
    r.json.return_value = body
    return r


def mealie_entry(scale=1.0, servings=4, yield_quantity=0, recipe=True):
    """Een maaltijd zoals GET /api/households/mealplans/{id} van Mealie v3.30.0 die geeft."""
    entry = {
        "id": 7, "date": "2026-11-03", "entryType": "dinner", "title": "", "text": "",
        "recipeId": "r1" if recipe else None, "recipeScale": scale,
        "groupId": "g1", "userId": "u1", "householdId": "h1",
        "recipe": {"id": "r1", "slug": "pasta", "name": "Pasta", "recipeServings": servings,
                   "recipeYieldQuantity": yield_quantity} if recipe else None,
    }
    return entry


def plan_entry(scale=1.0, recipe_id="r1", entry_id="7", date="2026-11-03"):
    entry = {"id": entry_id, "date": date, "recipeId": recipe_id, "recipe": {"id": recipe_id}}
    if scale is not None:
        entry["recipeScale"] = scale
    return entry


class PortionsCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"MEALIESHOPPER_DATA_DIR": tmp.name, "MEALIESHOPPER_AUTH_SECRET": "test-secret"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in ("MEALIE_URL", "MEALIE_API_TOKEN", "MEALIE_PUBLIC_URL", "JWT_SECRET", ENV_DEFAULT):
            os.environ.pop(key, None)
        auth._INITIALIZED = False
        self.client = create_app().test_client()

    def set_default(self, value):
        auth.set_setting(portions.SETTING_DEFAULT, str(value))


class EffectiveTest(PortionsCase):
    def test_yield_prefers_servings_then_yield_quantity(self):
        self.assertEqual(portions.recipe_yield({"recipeServings": 4}), 4.0)
        self.assertEqual(portions.recipe_yield({"recipeServings": 0, "recipeYieldQuantity": 6}), 6.0)
        self.assertIsNone(portions.recipe_yield({"recipeServings": 0, "recipeYieldQuantity": 0}))
        self.assertIsNone(portions.recipe_yield(None))

    def test_unknown_yield_is_not_scaled(self):
        self.set_default(2)
        result = portions.effective(plan_entry(), {"recipeServings": 0, "recipeYieldQuantity": 0})
        self.assertEqual(result["source"], "unknown")
        self.assertEqual(result["scale"], 1.0)
        self.assertIsNone(result["portions"])
        self.assertIsNone(result["recipeServings"])

    def test_text_only_entry_has_no_portions(self):
        self.set_default(2)
        entry = {"id": 9, "date": "2026-11-04", "recipeId": None, "recipeScale": 1.0, "recipe": None,
                 "title": "Restjes: chili (van maandag)"}
        self.assertEqual(portions.effective(entry, None),
                         {"portions": None, "scale": 1.0, "source": "unknown", "recipeServings": None})

    def test_entry_without_recipe_scale_is_unsupported(self):
        self.set_default(2)
        result = portions.effective(plan_entry(scale=None), {"recipeServings": 4})
        self.assertEqual(result["source"], "unsupported")
        self.assertEqual(result["scale"], 1.0)

    def test_marker_counts_as_set(self):
        self.set_default(2)
        portions.mark("7", "r1", "2026-11-03", "user:helmer")
        result = portions.effective(plan_entry(), {"recipeServings": 4})
        self.assertEqual(result["source"], "set")
        self.assertEqual(result["portions"], 4)
        self.assertEqual(result["scale"], 1.0)
        self.assertEqual(result["recipeServings"], 4.0)

    def test_marker_with_another_recipe_is_ignored(self):
        self.set_default(2)
        portions.mark("7", "x", "2026-11-03", "user:helmer")
        self.assertFalse(portions.is_marked("7", "y"))
        result = portions.effective(plan_entry(recipe_id="y"), {"recipeServings": 4})
        self.assertEqual(result["source"], "default")

    def test_unmark_removes_the_marker(self):
        portions.mark("7", "r1", "2026-11-03", "user:helmer")
        self.assertTrue(portions.is_marked("7", "r1"))
        portions.unmark("7")
        self.assertFalse(portions.is_marked("7", "r1"))

    def test_scale_other_than_one_counts_as_set(self):
        self.set_default(3)
        result = portions.effective(plan_entry(scale=0.5), {"recipeServings": 4})
        self.assertEqual(result["source"], "set")
        self.assertEqual(result["portions"], 2)
        self.assertEqual(result["scale"], 0.5)

    def test_default_applies_to_unset_meals(self):
        self.set_default(2)
        result = portions.effective(plan_entry(), {"recipeServings": 4})
        self.assertEqual(result["source"], "default")
        self.assertEqual(result["portions"], 2)
        self.assertEqual(result["scale"], 0.5)

    def test_without_default_the_recipe_yield_applies(self):
        result = portions.effective(plan_entry(), {"recipeServings": 4})
        self.assertEqual(result["source"], "recipe")
        self.assertEqual(result["portions"], 4)
        self.assertEqual(result["scale"], 1.0)

    def test_uneven_yield_rounds_the_portions_not_the_scale(self):
        self.set_default(2)
        result = portions.effective(plan_entry(), {"recipeServings": 3})
        self.assertEqual(result["portions"], 2)
        self.assertTrue(abs(result["scale"] - 2 / 3) < 1e-9)

    def test_default_reads_setting_before_env_and_rejects_junk(self):
        with mock.patch.dict(os.environ, {ENV_DEFAULT: "2"}):
            self.set_default("3")
            self.assertEqual(portions.default_portions(), 3)
            self.set_default("")
            self.assertEqual(portions.default_portions(), 2)
        for junk in ("0", "13", "twee"):
            with mock.patch.dict(os.environ, {ENV_DEFAULT: junk}):
                self.assertIsNone(portions.default_portions(), junk)
        self.assertIsNone(portions.default_portions(), "niets ingesteld")


class WritePortionsTest(PortionsCase):
    def setUp(self):
        super().setUp()
        auth.set_secret(mealie.SECRET_URL, URL)
        auth.set_secret(mealie.SECRET_TOKEN, "token")

    def mock_mealie(self, entry, put_status=200):
        """GET geeft `entry`; PUT geeft het meegestuurde terug (of een fout met `put_status`)."""
        get = mock.patch.object(mealie.requests, "get", return_value=response(body=entry))

        def put(url, **kwargs):
            if put_status >= 300:
                return response(status=put_status, text="kapot")
            return response(body=kwargs["json"])

        put_patch = mock.patch.object(mealie.requests, "put", side_effect=put)
        self.get, self.put = get.start(), put_patch.start()
        self.addCleanup(get.stop)
        self.addCleanup(put_patch.stop)

    def put_body(self):
        self.assertEqual(self.put.call_count, 1)
        self.assertEqual(self.put.call_args.args[0], f"{URL}/api/households/mealplans/7")
        return self.put.call_args.kwargs["json"]

    def test_set_writes_scale_and_marks(self):
        entry = mealie_entry(scale=1.0, servings=4)
        self.mock_mealie(entry)
        result = portions.set_portions("7", 2, "user:helmer")
        body = self.put_body()
        self.assertEqual(body["recipeScale"], 0.5)
        self.assertEqual({k: v for k, v in body.items() if k != "recipeScale"},
                         {k: v for k, v in entry.items() if k != "recipeScale"})
        for field in ("date", "id", "groupId", "userId"):
            self.assertIn(field, body)
        self.assertTrue(portions.is_marked("7", "r1"))
        self.assertEqual(result["portions"], 2)
        self.assertEqual(result["source"], "set")

    def test_set_equal_to_yield_writes_one_and_marks(self):
        self.mock_mealie(mealie_entry(scale=0.5, servings=4))
        self.set_default(2)
        result = portions.set_portions("7", 4, "user:helmer")
        self.assertEqual(self.put_body()["recipeScale"], 1.0)
        self.assertEqual(result["source"], "set")
        self.assertEqual(result["portions"], 4)

    def test_failed_put_stores_no_marker(self):
        self.mock_mealie(mealie_entry(), put_status=500)
        with self.assertRaises(RuntimeError) as ctx:
            portions.set_portions("7", 2, "user:helmer")
        self.assertIn("500", str(ctx.exception))
        self.assertFalse(portions.is_marked("7", "r1"))

    def test_timeout_stores_no_marker(self):
        self.mock_mealie(mealie_entry())
        self.put.side_effect = requests.Timeout("te traag")
        with self.assertRaises(RuntimeError):
            portions.set_portions("7", 2, "user:helmer")
        self.assertFalse(portions.is_marked("7", "r1"))

    def test_reset_writes_one_and_unmarks(self):
        portions.mark("7", "r1", "2026-11-03", "user:helmer")
        self.mock_mealie(mealie_entry(scale=0.5, servings=4))
        result = portions.reset("7", "user:helmer")
        self.assertEqual(self.put_body()["recipeScale"], 1.0)
        self.assertFalse(portions.is_marked("7", "r1"))
        self.assertEqual(result["source"], "recipe")

    def test_rejects_out_of_range_and_unknown_yield(self):
        self.mock_mealie(mealie_entry(servings=4))
        for value in (0, 13, True, "2", 2.5, None):
            with self.assertRaises(ValueError, msg=repr(value)):
                portions.set_portions("7", value, "user:helmer")
        self.mock_mealie(mealie_entry(servings=0, yield_quantity=0))
        with self.assertRaises(ValueError):
            portions.set_portions("7", 2, "user:helmer")
        with self.assertRaises(ValueError):
            portions.reset("7", "user:helmer")
        self.put.assert_not_called()

    def test_rejects_an_entry_without_recipe_or_recipe_scale(self):
        self.mock_mealie(mealie_entry(recipe=False))
        with self.assertRaises(ValueError):
            portions.set_portions("7", 2, "user:helmer")
        entry = mealie_entry()
        del entry["recipeScale"]
        self.mock_mealie(entry)
        with self.assertRaises(ValueError):
            portions.set_portions("7", 2, "user:helmer")
        self.put.assert_not_called()

    def test_routes_return_effective_and_map_errors(self):
        self.mock_mealie(mealie_entry(scale=1.0, servings=4))
        ok = self.client.put("/api/mealplan/7/portions", json={"portions": 2})
        self.assertEqual(ok.status_code, 200, ok.get_json())
        self.assertEqual(ok.get_json()["portions"], 2)
        self.assertEqual(ok.get_json()["source"], "set")

        bad = self.client.put("/api/mealplan/7/portions", json={"portions": 99})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("error", bad.get_json())
        self.assertEqual(self.client.put("/api/mealplan/7/portions", data="geen json").status_code, 400)

        self.mock_mealie(mealie_entry(scale=0.5, servings=4))
        reset = self.client.delete("/api/mealplan/7/portions")
        self.assertEqual(reset.status_code, 200, reset.get_json())
        self.assertEqual(reset.get_json()["source"], "recipe")

        self.mock_mealie(mealie_entry(), put_status=500)
        failed = self.client.put("/api/mealplan/7/portions", json={"portions": 2})
        self.assertEqual(failed.status_code, 424)
        self.assertIn("error", failed.get_json())


if __name__ == "__main__":
    unittest.main()
