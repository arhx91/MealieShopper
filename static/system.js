// Systeem (Instellingen > Systeem): Opschonen > Fabrieksinstellingen. De beheerder wist hier alle gegevens van de
// installatie, na een waarschuwing, het getypte woord en opnieuw inloggen (wachtwoord, code of passkey).
// Laadt na app.js en gebruikt daar de hulpfuncties van ($, message, jsonFetch, t, base64urlToBuffer, ...).

const factoryResetState = { info: null, busy: false };
const FACTORY_RESET_POLL_MS = 2000;
const FACTORY_RESET_GIVE_UP_MS = 90000;

async function openFactoryReset() {
  message("#reset-message", "");
  try {
    factoryResetState.info = await jsonFetch("/api/system/factory-reset");
  } catch (error) {
    message("#reset-message", error.message);
    return;
  }
  renderFactoryReset();
  $("#reset-form").hidden = false;
  $("#reset-actions").hidden = true;
  $("#reset-phrase").focus();
}

function closeFactoryReset() {
  $("#reset-form").reset();
  $("#reset-form").hidden = true;
  $("#reset-actions").hidden = false;
  message("#reset-message", "");
}

function renderFactoryReset() {
  const info = factoryResetState.info;
  const { password, mfa, passkey } = info.methods;
  const asksPassword = info.authRequired && password;
  $("#reset-password-field").hidden = !asksPassword;
  $("#reset-code-field").hidden = !(asksPassword && mfa);
  $("#reset-no-restart").hidden = info.restart;
  let hint = "system.reset_auth_none";
  if (info.authRequired) hint = password && passkey ? "system.reset_auth_both" : password ? "system.reset_auth_password" : "system.reset_auth_passkey";
  $("#reset-auth-hint").textContent = t(hint);
}

async function factoryResetPasskey() {
  const unavailable = webauthnUnavailableReason();
  if (unavailable) throw new Error(unavailable);
  const payload = await jsonFetch("/api/system/factory-reset/options", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  const options = payload.options;
  options.challenge = base64urlToBuffer(options.challenge);
  options.allowCredentials = (options.allowCredentials || []).map((credential) => ({
    ...credential,
    id: base64urlToBuffer(credential.id),
  }));
  const credential = await navigator.credentials.get({ publicKey: options });
  return publicKeyCredentialToJSON(credential);
}

// Wacht tot de app weg is geweest en terugkomt, en laad dan de pagina opnieuw (dan start de onboarding).
function waitForRestart() {
  const startedAt = Date.now();
  let wasDown = false;
  const poll = async () => {
    let up = false;
    try {
      up = (await fetch("/healthz", { cache: "no-store" })).ok;
    } catch (error) {
      up = false;
    }
    if (up && wasDown) {
      window.location.reload();
      return;
    }
    if (!up) wasDown = true;
    if (Date.now() - startedAt > FACTORY_RESET_GIVE_UP_MS) {
      message("#reset-message", t("system.reset_waiting_timeout"), "warning");
      return;
    }
    setTimeout(poll, FACTORY_RESET_POLL_MS);
  };
  setTimeout(poll, FACTORY_RESET_POLL_MS);
}

async function submitFactoryReset(event) {
  event.preventDefault();
  const info = factoryResetState.info;
  if (!info || factoryResetState.busy) return;
  const phrase = $("#reset-phrase").value.trim();
  if (phrase !== info.phrase) {
    message("#reset-message", t("system.reset_phrase_wrong", { phrase: info.phrase }));
    return;
  }
  const body = { confirm: phrase };
  const password = $("#reset-password").value;
  if (info.authRequired) {
    if (info.methods.password && password) {
      body.password = password;
      if (info.methods.mfa) body.code = $("#reset-code").value.trim();
    } else if (!info.methods.passkey) {
      message("#reset-message", t("system.reset_password_needed"));
      return;
    }
  }
  if (!window.confirm(t("system.reset_confirm"))) return;

  factoryResetState.busy = true;
  $("#reset-submit").disabled = true;
  $("#reset-cancel").disabled = true;
  message("#reset-message", t("system.reset_busy"), "warning");
  try {
    if (info.authRequired && !body.password) body.credential = await factoryResetPasskey();
    const result = await jsonFetch("/api/system/factory-reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("#reset-form").hidden = true;
    message("#reset-message", t(result.restarting ? "system.reset_done_restart" : "system.reset_done_manual"), "success");
    if (result.restarting) waitForRestart();
  } catch (error) {
    message("#reset-message", error.name === "NotAllowedError" ? t("passkey.cancelled") : error.message);
    $("#reset-submit").disabled = false;
    $("#reset-cancel").disabled = false;
  } finally {
    factoryResetState.busy = false;
  }
}

$("#reset-open").addEventListener("click", openFactoryReset);
$("#reset-cancel").addEventListener("click", closeFactoryReset);
$("#reset-form").addEventListener("submit", submitFactoryReset);

// Standaard porties (Instellingen > Systeem): voor hoeveel personen een maaltijd telt als niemand haar porties
// instelde. De waarde in de app gaat voor op MEALIESHOPPER_DEFAULT_PORTIONS; een leeg veld zet de app-waarde uit.
const defaultPortionsState = { settings: null };

function renderDefaultPortions() {
  const settings = defaultPortionsState.settings;
  if (!settings) return;
  const input = $("#default-portions");
  const fromApp = settings.defaultPortionsSource === "setting";
  if (document.activeElement !== input) input.value = fromApp ? String(settings.defaultPortions) : "";
  input.placeholder = settings.defaultPortionsSource === "env" ? String(settings.defaultPortions) : "";
  const note = {
    setting: "system.portions_note_setting",
    env: "system.portions_note_env",
    off: "system.portions_note_off",
  }[settings.defaultPortionsSource];
  $("#default-portions-note").textContent = note ? t(note, { count: settings.defaultPortions }) : "";
}

async function loadDefaultPortions() {
  try {
    defaultPortionsState.settings = await jsonFetch("/api/server-settings");
    renderDefaultPortions();
  } catch (error) {
    message("#default-portions-message", error.message);
  }
}

async function saveDefaultPortions(event) {
  const button = event.currentTarget;
  const input = $("#default-portions");
  message("#default-portions-message", "");
  // Tekst in een getalveld geeft value "" (badInput): niet als leeg opslaan, want dat zet de standaard stil uit.
  if (input.validity.badInput) {
    message("#default-portions-message", t("system.portions_invalid"));
    return;
  }
  const raw = input.value.trim();
  const value = raw === "" ? null : Number(raw);
  if (value !== null && !(Number.isInteger(value) && value >= 1 && value <= 12)) {
    message("#default-portions-message", t("system.portions_invalid"));
    return;
  }
  button.disabled = true;
  try {
    defaultPortionsState.settings = await jsonFetch("/api/server-settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ defaultPortions: value }),
    });
    // Bewaarde weken rekenden met de oude standaard: haal ze opnieuw op zodra het weekmenu weer open gaat.
    if (typeof mealPlanCache !== "undefined") mealPlanCache.clear();
    renderDefaultPortions();
    message("#default-portions-message", t("system.portions_saved"), "success");
  } catch (error) {
    message("#default-portions-message", error.message);
  } finally {
    button.disabled = false;
  }
}

$("#default-portions-save").addEventListener("click", saveDefaultPortions);
window.addEventListener("ms:languagechange", renderDefaultPortions);
