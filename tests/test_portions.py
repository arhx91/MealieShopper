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

    def test_unknown_yield_follows_a_scale_set_in_mealie(self):
        # Zonder opbrengst raden we geen porties, maar een factor die iemand in Mealie zette is bekend:
        # het mandje volgt die, net als de boodschappenlijst van Mealie zelf. De standaard telt hier niet.
        self.set_default(2)
        result = portions.effective(plan_entry(scale=1.5), {"recipeServings": 0, "recipeYieldQuantity": 0})
        self.assertEqual(result, {"portions": None, "scale": 1.5, "source": "unknown", "recipeServings": None})

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
        # Zoals update_meal_plan_entry: het hele item terug, zonder de velden die Mealie alleen bij het lezen geeft.
        expected = {k: v for k, v in entry.items() if k not in mealie._READ_ONLY_ENTRY_FIELDS and k != "recipeScale"}
        self.assertEqual({k: v for k, v in body.items() if k != "recipeScale"}, expected)
        for field in ("date", "id", "groupId", "userId"):
            self.assertIn(field, body)
        self.assertEqual(body["id"], 7)
        self.assertNotIn("recipe", body)
        self.assertNotIn("householdId", body)
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
        with self.assertLogs(mealie.log, "WARNING"), self.assertRaises(RuntimeError) as ctx:
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
        # Een maaltijd in Mealie heeft een geheel getal als id (v3.30.0); mealie._entry_id laat alleen dat door.
        self.mock_mealie(mealie_entry())
        uuid = "3f2a1b4c-9d8e-4f7a-b6c5-0123456789ab"
        for entry_id in ("../../users/self", "..", "1?x=", "%2F", "7/../8", "", None, "a" * 65, uuid, 0, -3, "7.5", True):
            with self.assertRaises(ValueError, msg=repr(entry_id)):
                portions.set_portions(entry_id, 2, "user:helmer")
            with self.assertRaises(ValueError, msg=repr(entry_id)):
                portions.reset(entry_id, "user:helmer")
        self.get.assert_not_called()
        self.put.assert_not_called()

    def test_accepts_the_integer_id_as_int_or_numeric_text(self):
        for entry_id in (7, "7", " 7 "):
            self.mock_mealie(mealie_entry(scale=1.0, servings=4))
            result = portions.set_portions(entry_id, 2, "user:helmer")
            self.assertEqual(result["portions"], 2, repr(entry_id))
            self.assertEqual(self.get.call_args.args[0], f"{URL}/api/households/mealplans/7")
            self.assertEqual(self.put_body()["id"], 7, repr(entry_id))
            self.assertTrue(portions.is_marked("7", "r1"), "de markering heeft één sleutel, hoe het id ook binnenkwam")
            portions.reset(entry_id, "user:helmer")
            self.assertFalse(portions.is_marked("7", "r1"))

    def test_a_failed_get_is_a_mealie_failure(self):
        self.mock_mealie(mealie_entry())
        self.get.side_effect = requests.ConnectionError("weg")
        with self.assertRaises(RuntimeError):
            portions.set_portions("7", 2, "user:helmer")
        self.get.side_effect = None
        self.get.return_value = response(status=404, body={"detail": "niet gevonden"}, text="niet gevonden")
        with self.assertLogs(mealie.log, "WARNING"), self.assertRaises(RuntimeError) as ctx:
            portions.reset("7", "user:helmer")
        self.assertIn("404", str(ctx.exception))
        self.put.assert_not_called()

    def test_a_failed_reset_keeps_the_marker(self):
        portions.mark("7", "r1", "2026-11-03", "user:helmer")
        self.mock_mealie(mealie_entry(scale=0.5), put_status=500)
        with self.assertLogs(mealie.log, "WARNING"), self.assertRaises(RuntimeError):
            portions.reset("7", "user:helmer")
        self.assertTrue(portions.is_marked("7", "r1"))

    def test_set_meal_plan_scale_sends_the_full_entry_like_update(self):
        entry = mealie_entry(scale=1.0)
        self.mock_mealie(entry)
        mealie.set_meal_plan_scale("7", 1.5)
        body = self.put_body()
        self.assertEqual(body, {**{k: v for k, v in entry.items() if k not in mealie._READ_ONLY_ENTRY_FIELDS}, "id": 7, "recipeScale": 1.5})
        for bad in (0, -1, float("nan"), float("inf"), True, "2"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                mealie.set_meal_plan_scale("7", bad)
        with self.assertRaises(ValueError):
            mealie.set_meal_plan_scale("../x", 1.0)
        self.assertEqual(self.put.call_count, 1, "een ongeldige vraag gaat niet naar Mealie")

    def test_another_recipe_drops_the_marker_via_upstreams_update(self):
        # Kiest iemand een ander recept (PATCH /api/mealplan/<id>), dan zet update_meal_plan_entry recipeScale op 1.0.
        # De markering hoort bij het oude recept en telt dan niet meer: de standaard geldt weer.
        self.set_default(2)
        self.mock_mealie(mealie_entry(scale=1.0, servings=4))
        portions.set_portions("7", 4, "user:helmer")
        self.assertEqual(self.put_body()["recipeScale"], 1.0)
        self.assertTrue(portions.is_marked("7", "r1"))
        self.assertEqual(portions.effective(mealie_entry(scale=1.0), {"recipeServings": 4})["source"], "set")

        other = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.mock_mealie(mealie_entry(scale=1.5, servings=4))
        answer = self.client.patch("/api/mealplan/7", json={"recipeId": other})
        self.assertEqual(answer.status_code, 200, answer.get_json())
        sent = self.put_body()
        self.assertEqual((sent["recipeId"], sent["recipeScale"]), (other, 1.0))
        after = {**sent, "recipe": {"id": other, "slug": "soep", "recipeServings": 4}}
        self.assertEqual(portions.effective(after, after["recipe"]),
                         {"portions": 2, "scale": 0.5, "source": "default", "recipeServings": 4.0})

    def test_route_only_takes_an_integer_id(self):
        # <int:entry_id>, zoals PATCH en DELETE /api/mealplan/<id>: een ander pad bestaat niet (404).
        self.mock_mealie(mealie_entry())
        for path in ("/api/mealplan/%2E%2E/portions", "/api/mealplan/1%3Fx%3D/portions", "/api/mealplan/-1/portions",
                     "/api/mealplan/3f2a1b4c-9d8e-4f7a-b6c5-0123456789ab/portions"):
            self.assertEqual(self.client.put(path, json={"portions": 2}).status_code, 404, path)
            self.assertEqual(self.client.delete(path).status_code, 404, path)
        zero = self.client.put("/api/mealplan/0/portions", json={"portions": 2})
        self.assertEqual(zero.status_code, 400)
        self.assertIn("error", zero.get_json())
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
        # De 424 schrijft in de log (mealie_action en _mealie_error); hier vangen we die op, zodat de testuitvoer schoon blijft.
        with self.assertLogs(self.client.application.logger, level="WARNING") as app_logs, \
                self.assertLogs(mealie.log, level="WARNING") as mealie_logs:
            failed = self.client.put("/api/mealplan/7/portions", json={"portions": 2})
        self.assertEqual(len(app_logs.records), 1)
        self.assertIn("Mealie error", app_logs.output[0])
        self.assertEqual(len(mealie_logs.records), 1)
        self.assertIn("500", mealie_logs.output[0])
        self.assertEqual(failed.status_code, 424)
        self.assertIn("Porties opslaan in Mealie mislukt (500)", failed.get_json()["error"])
        self.assertFalse(portions.is_marked("7", "r1"))


SPAGHETTI = {"quantity": 400, "unit": {"name": "gram", "abbreviation": "g", "useAbbreviation": True},
             "food": {"name": "spaghetti"}, "note": "", "display": "400 g spaghetti"}


class WeekServingsTest(PortionsCase):
    """`servings` per maaltijd in GET /api/mealplan: de vorm van upstream, de factor uit portions.effective."""

    def setUp(self):
        super().setUp()
        auth.set_secret(mealie.SECRET_URL, URL)
        auth.set_secret(mealie.SECRET_TOKEN, "token")

    def week(self, *items, servings=4, detail_ok=True):
        recipe = {"id": "r1", "slug": "pasta", "name": "Pasta", "recipeServings": servings, "recipeYieldQuantity": 0,
                  "recipeIngredient": [dict(SPAGHETTI)]}

        def get(url, **kwargs):
            if url.endswith("/mealplans"):
                return response(body={"items": list(items)})
            return response(body=recipe) if detail_ok else response(status=500, text="kapot")

        with mock.patch.object(mealie.requests, "get", side_effect=get), \
                mock.patch.object(mealie.requests, "put") as put, mock.patch.object(mealie.requests, "post") as post:
            answer = self.client.get("/api/mealplan?start=2026-11-02&end=2026-11-08")
        self.assertEqual(answer.status_code, 200, answer.get_json())
        put.assert_not_called()  # de standaard gaat nooit naar Mealie
        post.assert_not_called()
        return answer.get_json()

    @staticmethod
    def entry(entry_id=1, scale=1.0, servings=4):
        item = {"id": entry_id, "date": "2026-11-03", "entryType": "dinner", "recipeId": "r1",
                "recipe": {"id": "r1", "slug": "pasta", "name": "Pasta", "recipeServings": servings, "recipeYieldQuantity": 0}}
        if scale is not None:
            item["recipeScale"] = scale
        return item

    @staticmethod
    def quantity(entry):
        return entry["recipeDetail"]["recipeIngredient"][0]["quantity"]

    def test_the_default_scales_an_unset_meal(self):
        self.set_default(2)
        entry = self.week(self.entry())[0]
        self.assertEqual(entry["servings"], {"scale": 0.5, "base": 4, "planned": 2, "source": "default"})
        self.assertEqual(self.quantity(entry), 200)
        self.assertNotIn("portions", entry, "geen tweede veld naast servings")

    def test_with_an_uneven_yield_planned_is_exactly_the_default(self):
        self.set_default(2)
        entry = self.week(self.entry(servings=3), servings=3)[0]
        self.assertEqual(entry["servings"]["planned"], 2)
        self.assertIsInstance(entry["servings"]["planned"], int)
        self.assertAlmostEqual(entry["servings"]["scale"], 2 / 3)
        self.assertAlmostEqual(self.quantity(entry), 400 * 2 / 3, places=3)

    def test_without_a_default_the_recipe_applies(self):
        entry = self.week(self.entry())[0]
        self.assertEqual(entry["servings"], {"scale": 1.0, "base": 4, "planned": 4, "source": "recipe"})
        self.assertEqual(self.quantity(entry), 400)

    def test_a_marker_or_a_scale_counts_as_set_over_the_default(self):
        self.set_default(2)
        portions.mark("1", "r1", "2026-11-03", "user:helmer")
        marked, scaled = self.week(self.entry(1), self.entry(2, scale=1.5))
        self.assertEqual(marked["servings"], {"scale": 1.0, "base": 4, "planned": 4, "source": "set"})
        self.assertEqual(self.quantity(marked), 400)
        self.assertEqual(scaled["servings"], {"scale": 1.5, "base": 4, "planned": 6, "source": "set"})
        self.assertEqual(self.quantity(scaled), 600)

    def test_an_unknown_yield_follows_the_scale_in_mealie_not_the_default(self):
        self.set_default(2)
        entry = self.week(self.entry(scale=1.5, servings=0), servings=0)[0]
        self.assertEqual(entry["servings"], {"scale": 1.5, "base": None, "planned": None, "source": "unknown"})
        self.assertEqual(self.quantity(entry), 600)

    def test_a_mealie_without_recipe_scale_is_unsupported(self):
        self.set_default(2)
        entry = self.week(self.entry(scale=None))[0]
        self.assertEqual(entry["servings"], {"scale": 1.0, "base": 4, "planned": 4, "source": "unsupported"})
        self.assertEqual(self.quantity(entry), 400)

    def test_a_note_has_no_servings(self):
        self.set_default(2)
        note = {"id": 3, "date": "2026-11-04", "entryType": "dinner", "title": "Uit eten", "recipeId": None,
                "recipeScale": 1.0, "recipe": None}
        self.assertIsNone(self.week(note)[0]["servings"])

    def test_a_failed_recipe_fetch_uses_the_summary_for_the_yield(self):
        # M3: zonder het hele recept geeft de samenvatting in de maaltijd de opbrengst; dan geldt de standaard nog.
        self.set_default(2)
        entry = self.week(self.entry(), detail_ok=False)[0]
        self.assertIsNone(entry["recipeDetail"])
        self.assertEqual(entry["servings"], {"scale": 0.5, "base": 4, "planned": 2, "source": "default"})


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
