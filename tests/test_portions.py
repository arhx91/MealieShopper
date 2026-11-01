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

    def test_rejects_a_crafted_entry_id_before_asking_mealie(self):
        # Het nummer komt in het pad naar Mealie; ".." of "?" zou een ander Mealie-adres raken met ons token.
        self.mock_mealie(mealie_entry())
        for entry_id in ("../../users/self", "..", "1?x=", "%2F", "7/../8", "", None, "a" * 65):
            with self.assertRaises(ValueError, msg=repr(entry_id)):
                portions.set_portions(entry_id, 2, "user:helmer")
            with self.assertRaises(ValueError, msg=repr(entry_id)):
                portions.reset(entry_id, "user:helmer")
        self.get.assert_not_called()
        self.put.assert_not_called()

    def test_accepts_numeric_and_uuid_entry_ids(self):
        uuid = "3f2a1b4c-9d8e-4f7a-b6c5-0123456789ab"
        for entry_id in (7, "7", uuid):
            self.mock_mealie(mealie_entry(scale=1.0, servings=4))
            result = portions.set_portions(entry_id, 2, "user:helmer")
            self.assertEqual(result["portions"], 2, repr(entry_id))
            self.assertEqual(self.get.call_args.args[0], f"{URL}/api/households/mealplans/{entry_id}")
            portions.reset(entry_id, "user:helmer")

    def test_route_rejects_a_crafted_entry_id_with_400(self):
        self.mock_mealie(mealie_entry())
        for path in ("/api/mealplan/%2E%2E/portions", "/api/mealplan/1%3Fx%3D/portions"):
            put = self.client.put(path, json={"portions": 2})
            self.assertEqual(put.status_code, 400, path)
            self.assertIn("error", put.get_json())
            self.assertEqual(self.client.delete(path).status_code, 400, path)
        self.get.assert_not_called()
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
        # De 424 schrijft een waarschuwing in de log; hier vangen we die op, zodat de testuitvoer schoon blijft.
        with self.assertLogs(self.client.application.logger, level="WARNING") as logs:
            failed = self.client.put("/api/mealplan/7/portions", json={"portions": 2})
        self.assertEqual(len(logs.records), 1)
        self.assertIn("Porties opslaan in Mealie mislukt", logs.output[0])
        self.assertIn("500", logs.output[0])
        self.assertEqual(failed.status_code, 424)
        self.assertIn("error", failed.get_json())


class DefaultSettingTest(PortionsCase):
    def settings(self):
        response = self.client.get("/api/server-settings")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_payload_shows_default_and_source(self):
        payload = self.settings()
        self.assertIsNone(payload["defaultPortions"])
        self.assertEqual(payload["defaultPortionsSource"], "off")
        with mock.patch.dict(os.environ, {ENV_DEFAULT: "2"}):
            payload = self.settings()
            self.assertEqual((payload["defaultPortions"], payload["defaultPortionsSource"]), (2, "env"))
            self.set_default(3)
            payload = self.settings()
            self.assertEqual((payload["defaultPortions"], payload["defaultPortionsSource"]), (3, "setting"))
        with mock.patch.dict(os.environ, {ENV_DEFAULT: "twee"}):
            self.set_default("")
            payload = self.settings()
            self.assertEqual((payload["defaultPortions"], payload["defaultPortionsSource"]), (None, "off"))

    def test_source_helper_matches_default_portions(self):
        self.assertEqual(portions.default_portions_source(), (None, "off"))
        with mock.patch.dict(os.environ, {ENV_DEFAULT: "4"}):
            self.assertEqual(portions.default_portions_source(), (4, "env"))
            self.set_default(5)
            self.assertEqual(portions.default_portions_source(), (5, "setting"))
            self.assertEqual(portions.default_portions(), 5)

    def test_put_sets_and_clears_default(self):
        response = self.client.put("/api/server-settings", json={"defaultPortions": 4})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["defaultPortions"], 4)
        self.assertEqual(response.get_json()["defaultPortionsSource"], "setting")
        self.assertEqual(auth.get_setting(portions.SETTING_DEFAULT), "4")
        self.assertEqual(portions.default_portions(), 4)

        with mock.patch.dict(os.environ, {ENV_DEFAULT: "2"}):
            cleared = self.client.put("/api/server-settings", json={"defaultPortions": None})
            self.assertEqual(cleared.status_code, 200, cleared.get_json())
            self.assertEqual(auth.get_setting(portions.SETTING_DEFAULT), "")
            self.assertEqual((cleared.get_json()["defaultPortions"], cleared.get_json()["defaultPortionsSource"]), (2, "env"))

    def test_put_rejects_out_of_range(self):
        self.set_default(3)
        for junk in (0, 13, -1, 2.5, "4", True, "", [4]):
            response = self.client.put("/api/server-settings", json={"defaultPortions": junk})
            self.assertEqual(response.status_code, 400, junk)
            self.assertIn("error", response.get_json())
        self.assertEqual(auth.get_setting(portions.SETTING_DEFAULT), "3", "niets opgeslagen")

    def test_default_goes_along_in_a_backup(self):
        from mealieshopper import backup

        self.assertIn(portions.SETTING_DEFAULT, backup.PLAIN_SETTINGS)


if __name__ == "__main__":
    unittest.main()
