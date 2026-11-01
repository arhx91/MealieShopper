import os
import tempfile
import unittest
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper import auth, portions  # noqa: E402
from mealieshopper.app import create_app  # noqa: E402

ENV_DEFAULT = "MEALIESHOPPER_DEFAULT_PORTIONS"


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


if __name__ == "__main__":
    unittest.main()
