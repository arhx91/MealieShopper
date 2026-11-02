import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["PASSKEY_AUTH_ENABLED"] = "false"

from mealieshopper.app import create_app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
OB_JS = (ROOT / "static" / "onboarding.js").read_text(encoding="utf-8")


class MarkupTest(unittest.TestCase):
    def test_login_page_has_password_mfa_and_passkey_parts(self):
        gate = HTML[HTML.index('id="auth-gate"'):HTML.index('id="onboarding"')]
        for needle in ('id="login-form"', 'id="login-username"', 'autocomplete="username"',
                       'id="login-password"', 'autocomplete="current-password"', 'id="mfa-form"',
                       'autocomplete="one-time-code"', 'id="mfa-recovery-toggle"', 'id="login-passkey"'):
            self.assertIn(needle, gate, needle)
        self.assertRegex(gate, r'<input[^>]*id="login-password"[^>]*type="password"|<input[^>]*type="password"[^>]*id="login-password"')
        self.assertNotIn("auth-primary", HTML, "het oude passkey-aanmaakformulier is naar de installatiehulp verhuisd")

    def test_onboarding_shell_is_an_accessible_dialog_and_starts_hidden(self):
        shell = re.search(r'<section id="onboarding"[^>]*>', HTML).group(0)
        for needle in ('role="dialog"', 'aria-modal="true"', 'aria-labelledby="ob-title"', "hidden"):
            self.assertIn(needle, shell, needle)
        for needle in ('id="ob-progress"', 'id="ob-body"', 'id="ob-message"', 'role="alert"'):
            self.assertIn(needle, HTML, needle)

    def test_security_tab_manages_password_and_mfa(self):
        security = HTML[HTML.index('id="profile-security"'):HTML.index("</section>", HTML.index('id="profile-security"'))]
        for needle in ('id="password-form"', 'id="pw-current"', 'autocomplete="new-password"', 'id="mfa-panel"', 'id="credentials-list"'):
            self.assertIn(needle, security, needle)

    def test_system_tab_has_the_default_portions_field(self):
        system = HTML[HTML.index('id="settings-system"'):HTML.index('id="backup-box"')]
        field = re.search(r'<input[^>]*id="default-portions"[^>]*>', system)
        self.assertIsNotNone(field, "het veld #default-portions staat niet bij Systeem")
        for needle in ('type="number"', 'min="1"', 'max="12"'):
            self.assertIn(needle, field.group(0), needle)
        self.assertIn('id="default-portions-save"', system)
        system_js = (ROOT / "static" / "system.js").read_text(encoding="utf-8")
        self.assertRegex(system_js, r'jsonFetch\("/api/server-settings", \{\s*method: "PUT"')
        self.assertIn("defaultPortions", system_js)
        self.assertIn("loadDefaultPortions", APP_JS, "het tabblad Systeem laadt de instelling")

    def test_default_portions_labels_exist_in_both_languages(self):
        en = dictionary("en")
        for key in ("system.portions_title", "system.portions_intro", "system.portions_label", "system.portions_save",
                    "system.portions_saved", "system.portions_note_setting", "system.portions_note_env",
                    "system.portions_note_off"):
            self.assertTrue(key in NL, f"{key} ontbreekt in nl.js")
            self.assertTrue(key in en, f"{key} ontbreekt in en.js")

    @staticmethod
    def function_body(name):
        match = re.search(rf"^(?:async\s+)?function\s+{name}\s*\(.*?^\}}", APP_JS, re.M | re.S)
        return match.group(0) if match else ""

    def test_meal_cards_have_a_portions_stepper(self):
        render = self.function_body("renderMeal")
        self.assertIn("mealPortionsHtml(entry)", render, "renderMeal tekent de knoppen voor de porties")
        # Upstreams "4 porties" (mealServings) alleen als er geen knoppen zijn: zo staat het aantal er één keer.
        self.assertRegex(render, r'stepper \? "" : mealServings\(entry\)')
        # De hele maaltijd is een knop (wijzigen); knoppen mogen daar niet in. De porties staan erna, in dezelfde groep.
        self.assertLess(render.rindex("</button>"), render.index("meal-group__portions"))
        stepper = self.function_body("mealPortionsHtml")
        for needle in ("data-portions-minus", "data-portions-plus", "aria-label", "data-portions-reset", "entry.servings"):
            self.assertIn(needle, stepper, needle)
        # Alleen bij "set" een link terug naar de standaard; bij "unsupported" niets; zonder recept niets.
        self.assertIn('"unsupported"', stepper)
        self.assertIn('"unknown"', stepper)
        for needle in (".meal-portions", ".meal-group__portions"):
            self.assertIn(needle, CSS, needle)

    def test_portion_changes_reload_the_week_like_a_plan_change(self):
        # Na een wijziging tonen kaart en lijst meteen de nieuwe aantallen: zoals na een PATCH (refreshMealPlanAfterChange).
        for name in ("setMealPortions", "resetMealPortions"):
            body = self.function_body(name)
            self.assertTrue(body, f"{name} ontbreekt")
            self.assertIn("await refreshMealPlanAfterChange(", body, name)
            self.assertIn("/portions", body, name)
        self.assertIn('method: "PUT"', self.function_body("setMealPortions"))
        self.assertIn('method: "DELETE"', self.function_body("resetMealPortions"))
        self.assertNotIn("reloadWeekAfterPortions", APP_JS, "geen eigen herlaadweg naast die van upstream")

    def test_one_portion_change_at_a_time(self):
        change = self.function_body("changeMealPortions")
        self.assertIn("if (mealPortionsBusy) return;", change)
        self.assertIn("mealPortionsBusy = true;", change)
        self.assertLess(change.index("finally"), change.rindex("mealPortionsBusy = false;"))

    def test_portion_changes_drop_the_basket_check_list(self):
        # Na een gelukte wijziging klopt een open controlelijst van het mandje niet meer: die gaat weg, net als in
        # loadMealPlan, en een voorbeeld dat nog liep van vóór de wijziging vult het mandje niet meer.
        for name in ("setMealPortions", "resetMealPortions"):
            body = self.function_body(name)
            self.assertIn("dropCartReview()", body, name)
            self.assertLess(body.index("jsonFetch"), body.index("dropCartReview()"), f"{name}: pas na een gelukte wijziging")
            self.assertLess(body.index("dropCartReview()"), body.index("refreshMealPlanAfterChange"), name)
        # Een andere week tonen (bladeren, sync, nieuwe standaard) maakt een voorbeeld dat nog liep ook ongeldig.
        load = self.function_body("loadMealPlan")
        self.assertIn("dropCartReview()", load)
        self.assertLess(load.index("dropCartReview()"), load.index("cachedMealPlan(week)"))
        drop = self.function_body("dropCartReview")
        self.assertIn("cartGeneration++", drop)
        self.assertIn('$("#cart-result").innerHTML = ""', drop)
        self.assertRegex(APP_JS, re.compile(r"^let cartGeneration = 0;", re.M))
        add = self.function_body("addToCart")
        self.assertIn("const generation = ++cartGeneration;", add)
        # Na het wachten op de server: een oud voorbeeld (in het gelukte en het mislukte pad) tekent niets.
        self.assertEqual(add.count("if (generation !== cartGeneration) return;"), 2, add)
        self.assertLess(add.index("await jsonFetch"), add.index("if (generation !== cartGeneration) return;"))

    def test_default_portions_rejects_text_instead_of_clearing(self):
        # Tekst in een type=number-veld geeft value "" (badInput); dat mag de standaard niet stil uitzetten.
        system_js = (ROOT / "static" / "system.js").read_text(encoding="utf-8")
        save = re.search(r"^async function saveDefaultPortions\(.*?^\}", system_js, re.M | re.S).group(0)
        self.assertIn("validity.badInput", save)
        self.assertLess(save.index("validity.badInput"), save.index("jsonFetch"))
        self.assertRegex(save, r'validity\.badInput[^;]*\)\s*\{\s*message\("#default-portions-message", t\("system\.portions_invalid"\)\);\s*return;')

    def test_portion_labels_exist_in_both_languages(self):
        en = dictionary("en")
        # Het aantal zelf is upstreams "plan.servings" (een meervoudsvorm: "1 portie", "4 porties").
        for key in ("plan.servings.one", "plan.servings.other", "plan.portions_yield", "plan.portions_set", "plan.portions_default",
                    "plan.portions_recipe", "plan.portions_unknown", "plan.portions_reset", "plan.portions_less", "plan.portions_more",
                    "plan.portions_scale_mealie"):
            self.assertTrue(key in NL, f"{key} ontbreekt in nl.js")
            self.assertTrue(key in en, f"{key} ontbreekt in en.js")
        self.assertFalse("plan.portions.other" in NL, "geen tweede tekst voor hetzelfde aantal")
        self.assertEqual(NL["plan.portions_set"], "ingesteld")
        self.assertEqual(NL["plan.portions_default"], "standaard")
        self.assertEqual(NL["plan.portions_recipe"], "recept")
        self.assertEqual(NL["plan.portions_unknown"], "recept zonder porties")
        self.assertEqual(en["plan.portions_set"], "set")
        self.assertEqual(en["plan.portions_default"], "default")
        self.assertEqual(en["plan.portions_recipe"], "recipe")
        self.assertEqual(en["plan.portions_unknown"], "recipe without servings")
        self.assertEqual(NL["plan.portions_scale_mealie"], "\u00d7{factor} uit Mealie")
        self.assertEqual(en["plan.portions_scale_mealie"], "\u00d7{factor} from Mealie")

    def test_scripts_load_in_order(self):
        self.assertLess(HTML.index("app.js"), HTML.index("onboarding.js"))

    def test_page_is_served_with_both_scripts_and_the_vendored_qr_code(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, {"MEALIESHOPPER_DATA_DIR": tmp.name}):
            client = create_app().test_client()
            html = client.get("/").get_data(as_text=True)
            self.assertIn("onboarding.js?v=", html)
            self.assertEqual(client.get("/static/onboarding.js").status_code, 200)
            qr = client.get("/static/vendor/qrcode-2.0.4.js")
            self.assertEqual(qr.status_code, 200)
            self.assertIn("Kazuhiko Arase", qr.get_data(as_text=True)[:400])
            self.assertEqual(client.get("/static/vendor/LICENSE-qrcode.txt").status_code, 200)


def dictionary(lang: str) -> dict[str, str]:
    source = (ROOT / "static" / "i18n" / f"{lang}.js").read_text(encoding="utf-8")
    import json

    return {json.loads(f'"{k}"'): json.loads(f'"{v}"') for k, v in re.findall(r'^  "([^"]+)": "((?:[^"\\]|\\.)*)",$', source, re.M)}


NL = dictionary("nl")


class ScriptTest(unittest.TestCase):
    def test_six_steps_in_the_asked_order(self):
        ids = re.findall(r'\{ id: "(\w+)" \}', OB_JS)
        self.assertEqual(ids, ["auth", "mealie", "ah", "jumbo", "picnic", "done"])
        self.assertEqual([NL[f"ob.step.{i}"] for i in ids], ["Beveiliging", "Mealie", "Albert Heijn", "Jumbo", "Picnic", "Klaar"])

    def test_security_step_follows_the_domain_rule(self):
        for needle in ("status.passkeyAvailable", "go-passkey", "go-password", "go-open", "passkeyUnavailableReason"):
            self.assertIn(needle, OB_JS, needle)
        self.assertIn("Passkeys werken op dit adres niet", NL["ob.nopasskey.title"])
        self.assertIn("Zonder beveiliging verder", NL["ob.skip_security"])
        # Zonder domeinnaam is het wachtwoord de aanbevolen keuze en is overslaan mogelijk;
        # met een domeinnaam staat de passkey bovenaan en is er geen 'zonder beveiliging'.
        choose = OB_JS[OB_JS.index("body.innerHTML = status.passkeyAvailable"):OB_JS.index("function obBackButton")]
        domain_part, no_domain_part = choose.split(": `", 1)
        self.assertNotIn("go-open", domain_part)
        self.assertIn("go-open", no_domain_part)

    def test_mfa_is_optional_and_recovery_codes_must_be_acknowledged(self):
        for needle in ("skip-mfa", "data-recovery-saved", "disabled"):
            self.assertIn(needle, OB_JS, needle)
        self.assertIn("Overslaan", NL["ob.mfa.skip"])
        self.assertIn("niet meer getoond", NL["mfa.recovery.text"])

    def test_explanations_for_mealie_and_ah(self):
        mealie = " ".join(NL[k] for k in NL if k.startswith("ob.mealie."))
        ah = " ".join(NL[k] for k in NL if k.startswith("ob.ah."))
        for needle in ("API-tokens", "Token genereren", "http://mealie:9000"):
            self.assertIn(needle, mealie, needle)
        for needle in ("appie://login-exit?code=", "ontwikkelaarsconsole", "computer"):
            self.assertIn(needle, ah, needle)

    def test_mealie_and_ah_are_linked_inside_the_wizard(self):
        # Geen sprong naar Beheer: de formulieren staan in de stappen en je gaat daarna gewoon door.
        for needle in ('data-ob-form="mealie"', 'data-ob-form="ah"', '"/api/mealie/auth"', '"/api/ah/auth"',
                       "obIntegrationDone", 'id="ob-mealie-next"', 'id="ob-ah-next"', 'id="ob-mealie-skip"', 'id="ob-ah-skip"'):
            self.assertIn(needle, OB_JS, needle)
        self.assertNotIn("finish-mealie", OB_JS)
        self.assertNotIn("openSettings", OB_JS)
        self.assertNotIn("openProfile", OB_JS)

    def test_jumbo_and_picnic_are_linked_inside_the_wizard_and_can_be_skipped(self):
        for needle in ('data-ob-form="jumbo"', '"/api/jumbo/auth"', '"/api/jumbo/auth/status"', 'href="/api/jumbo/auth/start"',
                       'id="ob-jumbo-next"', 'id="ob-jumbo-skip"',
                       'data-ob-form="picnic"', 'data-ob-form="picnic-code"', '"/api/picnic/auth"', '"/api/picnic/auth/status"',
                       '"/api/picnic/auth/2fa/request"', '"/api/picnic/auth/2fa/verify"', "twoFactorRequired", "twoFactorPending",
                       'id="ob-picnic-next"', 'id="ob-picnic-skip"', "refreshJumboStatus", "refreshPicnicStatus"):
            self.assertIn(needle, OB_JS, needle)
        # De volgorde van de knoppen: AH > Jumbo > Picnic > klaar, en de samenvatting kan naar elke stap terug.
        for target, action in (("ah", "go-jumbo"), ("jumbo", "go-picnic"), ("picnic", "go-done")):
            self.assertIn(f'id="ob-{target}-next" data-ob-action="{action}"', OB_JS, target)
        for action in ("go-jumbo", "go-picnic"):
            self.assertIn(f'data-ob-action="{action}">${{escapeHtml(t("ob.done.link_', OB_JS, action)

    def test_explanations_for_jumbo_and_picnic(self):
        jumbo = " ".join(NL[k] for k in NL if k.startswith(("ob.jumbo.", "jumbo.step")))
        picnic = " ".join(NL[k] for k in NL if k.startswith("ob.picnic."))
        for needle in ("jumbo://callbacks/login?code=", "ontwikkelaarsconsole", "computer"):
            self.assertIn(needle, jumbo, needle)
        for needle in ("e-mailadres", "wachtwoord", "sms"):
            self.assertIn(needle, picnic, needle)

    def test_opt_out_needs_an_explicit_confirmation(self):
        self.assertIn("ob-open-confirm", OB_JS)
        self.assertIn('postJson("/api/auth/open", { confirm: true })', OB_JS)

    def test_user_values_are_escaped(self):
        self.assertIn("escapeHtml(setup.otpauthUri)", OB_JS)
        self.assertIn("escapeHtml(code)", OB_JS)
        self.assertNotIn("innerHTML = error", OB_JS)

    def test_app_waits_for_the_last_script_before_starting(self):
        self.assertIn('document.addEventListener("DOMContentLoaded"', APP_JS)

    def test_login_page_hides_when_the_wizard_takes_over(self):
        self.assertIn("onboardingVisible()", APP_JS)
        self.assertIn("!auth.setupRequired", APP_JS)

    def test_security_blocks_fold_away_when_a_passkey_exists(self):
        self.assertIn('collapsible: hasPasskey', OB_JS)
        # Tweestapsverificatie is geen eigen blok meer: het zit in het wachtwoordblok.
        for name in ("password",):
            self.assertIn(f'id="{name}-toggle"', HTML)
            self.assertIn(f'id="{name}-body"', HTML)
            self.assertIn(f'id="{name}-badge"', HTML)
        self.assertNotIn('id="mfa-toggle"', HTML)
        body = HTML[HTML.index('id="password-body"'):HTML.index('id="credentials-list"')]
        self.assertIn('id="mfa-panel"', body)
        self.assertIn("Niet nodig", NL["sec.not_needed"])
        self.assertIn("Niet nodig", NL["sec.password.intro_passkey"])
        self.assertIn("passkey", NL["sec.password.intro_passkey"])
        self.assertIn("Niet nodig", NL["sec.mfa.optional_note"])
        self.assertIn("gebruikersnaam en wachtwoord", NL["sec.mfa.optional_note"])


class StyleTest(unittest.TestCase):
    def test_overlay_covers_the_page_and_scrolls(self):
        rule = re.search(r"\n\.onboarding \{(.*?)\n\}", CSS, re.S).group(1)
        for declaration in ("position: fixed", "inset: 0", "overflow-y: auto", "z-index: 100"):
            self.assertIn(declaration, rule)
        self.assertIn("env(safe-area-inset-bottom)", rule)
        self.assertIn("body.has-onboarding", CSS)

    def test_qr_code_stays_black_on_white(self):
        self.assertRegex(re.search(r"\n\.qr \{(.*?)\n\}", CSS, re.S).group(1), r"background: #fff")

    def test_phone_layout_stacks_actions_and_single_qr_column(self):
        phone = CSS[CSS.index(".onboarding__card {\n    padding: 18px 16px;"):]
        phone = phone[:phone.index("\n}\n")]
        self.assertIn("flex-direction: column-reverse", phone)
        self.assertIn("grid-template-columns: 1fr;", phone)

    def test_checkbox_and_button_targets_are_large_enough(self):
        self.assertIn("width: 20px", re.search(r"\n\.check input \{(.*?)\n\}", CSS, re.S).group(1))
        self.assertIn("min-height: var(--control-height)", re.search(r"\n\.link-button \{(.*?)\n\}", CSS, re.S).group(1))


def top_level(name: str) -> str:
    """De broncode van een functie of constante op het hoogste niveau van app.js."""
    match = re.search(rf"^(?:async\s+)?function\s+{name}\s*\(.*?^\}}", APP_JS, re.M | re.S) or re.search(
        rf"^const {name} =.*?;$", APP_JS, re.M | re.S
    )
    if not match:
        raise AssertionError(f"{name} niet gevonden in app.js")
    return match.group(0)


@unittest.skipUnless(shutil.which("node"), "Node is niet geïnstalleerd")
class MealPortionsRenderTest(unittest.TestCase):
    """De knoppen voor de porties op upstreams maaltijdkaart, echt getekend in Node met de Nederlandse teksten."""

    PARTS = ("MDI_PENCIL_OUTLINE", "mdiIcon", "escapeHtml", "mealLabel", "mealName", "mealServings", "MIN_PORTIONS",
             "MAX_PORTIONS", "PORTIONS_SOURCE_LABELS", "clampPortions", "mealPortionsHtml", "renderMeal")

    def render(self, entries):
        script = "\n".join([
            "const I18N = { formatNumber: (v) => Number(v).toLocaleString('nl-NL'), messages: { nl: MESSAGES } };",
            "function fill(text, params) { return text.replace(/\\{(\\w+)\\}/g, (m, k) => (k in params ? String(params[k]) : m)); }",
            "function t(key, params = {}) { return fill(MESSAGES[key] ?? key, params); }",
            "function tn(key, count, params = {}) { return t(`${key}.${Number(count) === 1 ? 'one' : 'other'}`, { count: I18N.formatNumber(count), ...params }); }",
            *[top_level(name) for name in self.PARTS],
            f"process.stdout.write(JSON.stringify({json.dumps(entries)}.map((e) => [mealPortionsHtml(e), renderMeal(e)])));",
        ])
        script = f"const MESSAGES = {json.dumps(NL)};\n" + script
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def entry(source, planned=4, base=4, scale=1.0, **extra):
        return {"id": 7, "date": "2026-11-03", "entryType": "dinner", "recipeId": "r1",
                "recipe": {"id": "r1", "name": "Pasta", "slug": "pasta"}, "recipeDetail": {"name": "Pasta", "recipeIngredient": [{}, {}]},
                "servings": {"scale": scale, "base": base, "planned": planned, "source": source}, **extra}

    @staticmethod
    def text(html):
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def test_set_default_and_recipe(self):
        (set_html, set_card), (default_html, _), (recipe_html, _) = self.render(
            [self.entry("set", planned=6, scale=1.5), self.entry("default", planned=2, scale=0.5), self.entry("recipe")])
        self.assertIn("\u2212 6 porties +", self.text(set_html))
        self.assertIn("recept: 4 \u00b7 ingesteld standaard", self.text(set_html), "met de link terug naar de standaard")
        self.assertIn('data-portions-reset="7"', set_html)
        self.assertIn('data-portions-minus="7" data-portions-target="5"', set_html)
        self.assertIn('data-portions-plus="7" data-portions-target="7"', set_html)
        self.assertIn("recept: 4 \u00b7 standaard", self.text(default_html))
        self.assertIn("meal-portions__value--default", default_html)
        self.assertNotIn("data-portions-reset", default_html)
        self.assertIn("recept: 4 \u00b7 recept", self.text(recipe_html))
        self.assertNotIn("data-portions-reset", recipe_html)
        # Het aantal staat één keer op de kaart: in de knoppen, niet ook in upstreams regel "6 porties · 2 ingrediënten".
        self.assertEqual(self.text(set_card).count("6 porties"), 1, set_card)
        self.assertIn("2 ingrediënten", self.text(set_card))
        # De knoppen staan na de knop van de maaltijd, niet erin.
        self.assertLess(set_card.index("</button>"), set_card.index('class="meal-portions"'))

    def test_the_buttons_stop_at_one_and_twelve(self):
        (low, _), (high, _), (odd, _) = self.render([self.entry("set", planned=1), self.entry("set", planned=12), self.entry("set", planned=4.5)])
        self.assertRegex(low, r'data-portions-minus="7" data-portions-target="1"[^>]*disabled')
        self.assertRegex(high, r'data-portions-plus="7" data-portions-target="12"[^>]*disabled')
        # Een factor uit Mealie kan een half aantal geven; de knoppen schrijven altijd een geheel aantal:
        # min gaat naar het hele getal eronder, plus naar het hele getal erboven.
        self.assertIn('data-portions-minus="7" data-portions-target="4"', odd)
        self.assertIn('data-portions-plus="7" data-portions-target="5"', odd)

    def test_a_half_portion_is_shown_as_is_and_steps_to_the_whole_numbers_around_it(self):
        # Het getal is servings.planned van de server (één decimaal), met de Nederlandse komma; niet afgerond tot 3.
        (half, card), (low, _), (high, _), (tiny, _), (big, _) = self.render(
            [self.entry("set", planned=2.5, scale=0.625), self.entry("set", planned=1.5), self.entry("set", planned=11.5),
             self.entry("set", planned=0.5), self.entry("set", planned=13)])
        self.assertIn("\u2212 2,5 porties +", self.text(half))
        self.assertNotIn("3 porties", self.text(half + card))
        self.assertRegex(half, r'data-portions-minus="7" data-portions-target="2"(?![^>]*disabled)')
        self.assertRegex(half, r'data-portions-plus="7" data-portions-target="3"(?![^>]*disabled)')
        # 1,5: min mag nog (naar 1); 11,5: plus mag nog (naar 12).
        self.assertRegex(low, r'data-portions-minus="7" data-portions-target="1"(?![^>]*disabled)')
        self.assertRegex(high, r'data-portions-plus="7" data-portions-target="12"(?![^>]*disabled)')
        # Buiten 1-12 (een factor in Mealie zelf): het getal zoals het is, de knop die verder weg gaat staat uit.
        self.assertIn("0,5 porties", self.text(tiny))
        self.assertRegex(tiny, r'data-portions-minus="7" data-portions-target="1"[^>]*disabled')
        self.assertRegex(tiny, r'data-portions-plus="7" data-portions-target="1"(?![^>]*disabled)')
        self.assertRegex(big, r'data-portions-minus="7" data-portions-target="12"(?![^>]*disabled)')
        self.assertRegex(big, r'data-portions-plus="7" data-portions-target="12"[^>]*disabled')

    def test_an_unknown_yield_has_disabled_buttons_and_names_a_scale(self):
        (scaled, card), (plain, _) = self.render([self.entry("unknown", planned=None, base=None, scale=1.5),
                                                 self.entry("unknown", planned=None, base=None, scale=1.0)])
        self.assertEqual(scaled.count(" disabled"), 2)
        self.assertIn("recept zonder porties", self.text(scaled))
        self.assertIn("\u00d71,5 uit Mealie", self.text(scaled))
        self.assertNotIn("uit Mealie", plain)
        self.assertNotIn("data-portions-reset", scaled)
        self.assertNotIn("\u00d71,5 \u00b7", self.text(card), "upstreams schaal staat er niet nog eens")

    def test_no_stepper_on_unsupported_or_a_note(self):
        note = {"id": 9, "date": "2026-11-04", "entryType": "dinner", "title": "Uit eten", "recipeId": None, "recipe": None,
                "recipeDetail": None, "servings": None}
        (unsupported, card), (empty, note_card) = self.render([self.entry("unsupported"), note])
        self.assertEqual((unsupported, empty), ("", ""))
        self.assertIn("4 porties \u00b7 2 ingrediënten", self.text(card), "upstreams regel blijft bij een oudere Mealie")
        self.assertNotIn("meal-group", card + note_card)


if __name__ == "__main__":
    unittest.main()
