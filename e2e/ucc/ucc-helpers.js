/*
 * Generic Playwright helpers for UCC-built Splunk add-ons.
 *
 * Nothing in this file knows about any one add-on: the app name, page titles,
 * field labels and conf names are all passed in by the spec files. Copy the
 * whole e2e/ucc/ directory into another UCC TA unchanged and write that TA's
 * own specs beside it (see the adoption notes at the bottom of this file).
 *
 * Two rules the helpers follow, and specs built on them should keep:
 *
 *  - The UI is driven the way a person drives it: controls are found by their
 *    visible label and role, never by the UCC field name. A defect that maps a
 *    label to the wrong field therefore reaches the assertion instead of being
 *    hidden by a test that looks the control up under the same wrong name.
 *
 *  - What the UI saved is read back from splunkd's raw configuration endpoints
 *    (configs/conf-<file>), a route the UCC pages and REST handlers never use.
 *    Reading back through the add-on's own <restRoot>_* handler would agree
 *    with any bug in that handler.
 *
 * Environment: SPLUNK_WEB_URL (Playwright baseURL), SPLUNK_MGMT_URL,
 * SPLUNK_USER, SPLUNK_PASSWORD. Defaults suit the CI docker harness.
 */
const { expect, request } = require('@playwright/test');

const MGMT_URL = (process.env.SPLUNK_MGMT_URL || 'https://localhost:8089').replace(/\/$/, '');
const USER = process.env.SPLUNK_USER || 'admin';
const PASS = process.env.SPLUNK_PASSWORD || 'Changeme1!';
const MASK = '******';

const escapeRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * The globalConfig.json the page actually loads. ucc-gen migrates an older
 * globalConfig.json while it builds (a named logging tab becomes a typed
 * loggingTab, field types and validators change), so prefer the built copy in
 * stage/ or output/ and fall back to the repo's source file.
 */
function loadGlobalConfig(repoDir) {
    const fs = require('fs');
    const path = require('path');
    const src = JSON.parse(fs.readFileSync(path.join(repoDir, 'globalConfig.json'), 'utf8'));
    for (const top of ['stage', 'output']) {
        const built = path.join(repoDir, top, src.meta.name, 'appserver', 'static', 'js', 'build', 'globalConfig.json');
        if (fs.existsSync(built)) return JSON.parse(fs.readFileSync(built, 'utf8'));
    }
    return src;
}

/** A Configuration tab by name, or the logging tab by name or type. */
function configTab(gc, name) {
    return gc.pages.configuration.tabs.find((t) => t.name === name
        || (name === 'logging' && t.type === 'loggingTab'));
}

/** A tab's visible title (UCC titles a typed loggingTab "Logging"). */
const tabTitle = (t) => t.title || (t.type === 'loggingTab' ? 'Logging' : t.name);

// ---------------------------------------------------------------------------
// Session and navigation
// ---------------------------------------------------------------------------

/** Log in to Splunk Web. Waits on the URL pathname: the login page's own URL
 *  carries ?return_to=/app/..., so a regex over the whole URL matches before
 *  sign-in has finished (Data Dictionary CI, 10.6, Oct 2026). */
async function login(page, user = USER, pw = PASS) {
    await page.goto('/en-US/account/login');
    await page.locator('input[name="username"]').fill(user);
    await page.locator('input[name="password"]').fill(pw);
    await page.locator('input[name="password"]').press('Enter');
    await page.waitForURL((u) => !/\/account\/login/.test(u.pathname), { timeout: 60000 });
}

/** Collect uncaught page errors and console errors that come from the add-on's
 *  own static files. Splunk's own chrome logs unrelated noise, so only errors
 *  whose source or stack names the app count. Call before navigating. */
function watchAppErrors(page, app) {
    const errors = [];
    const ours = (s) => String(s || '').includes(`/app/${app}/`) || String(s || '').includes(`/static/app/${app}`);
    page.on('pageerror', (err) => {
        if (ours(err.stack) || ours(err.message)) errors.push(`pageerror: ${err.message}`);
    });
    page.on('console', (msg) => {
        if (msg.type() === 'error' && ours(msg.location().url)) errors.push(`console: ${msg.text()}`);
    });
    return {
        errors,
        assertNone: () => expect(errors, `app errors in the browser:\n${errors.join('\n')}`).toEqual([]),
    };
}

/** Open a UCC page (configuration, inputs, ...) and wait until it has drawn
 *  its title, so the page bundle loaded and globalConfig parsed. */
async function gotoUccPage(page, app, view, title) {
    await page.goto(`/en-US/app/${app}/${view}`);
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible({ timeout: 60000 });
}

/** Select a Configuration page tab by its visible title. */
async function openTab(page, title) {
    const tab = page.getByRole('tab', { name: title, exact: true });
    await tab.click();
    await expect(tab).toHaveAttribute('aria-selected', 'true');
}

// ---------------------------------------------------------------------------
// Forms
// ---------------------------------------------------------------------------

/** A regex for a control's accessible name: react-ui renders the required
 *  marker as a "*" span inside the <label>, before the text. */
const labelRe = (label) => new RegExp(`^\\s*\\*?\\s*${escapeRe(label)}\\s*\\*?\\s*$`);

/** The Page that owns `scope` (a Page or a Locator). Locators passed to
 *  filter({ has }) must be built from the page, not from the scope. */
const pageOf = (scope) => (typeof scope.page === 'function' ? scope.page() : scope);

/** An XPath string literal for `s` (handles both quote kinds). */
function xpathLiteral(s) {
    if (!s.includes('"')) return `"${s}"`;
    if (!s.includes("'")) return `'${s}'`;
    return `concat(${s.split('"').map((p) => `"${p}"`).join(', \'"\', ')})`;
}

/** The control-group wrapper (react-ui ControlGroup) whose <label> reads
 *  `label`. A field with a tooltip carries the tooltip text inside the
 *  <label> as screen-reader content, so the label's own text node is matched
 *  as well as its whole text. */
function controlGroup(scope, label) {
    const page = pageOf(scope);
    const whole = page.locator('label').filter({ hasText: labelRe(label) });
    const own = page.locator(`xpath=.//label[text()[normalize-space()=${xpathLiteral(label)}]]`);
    return scope.locator('[data-test="control-group"]').filter({ has: whole.or(own) });
}

/** The input control labelled `label` inside `scope` (a page, a dialog or a
 *  tab panel). UCC wraps every control in a ControlGroup whose <label> carries
 *  the globalConfig label, linked to the input by aria-labelledby; the
 *  structural route (the wrapper that holds that label) is the fallback. The
 *  UCC field name is never consulted.
 */
function control(scope, label) {
    const byAria = scope.getByRole('textbox', { name: labelRe(label) });
    const byStructure = controlGroup(scope, label).locator('input, textarea');
    return byAria.or(byStructure).first();
}

async function fillText(scope, label, value) {
    const box = control(scope, label);
    await box.click();
    await box.fill('');
    await box.fill(String(value));
    await expect(box).toHaveValue(String(value));
}

async function readText(scope, label) {
    return control(scope, label).inputValue();
}

/** Pick `option` from the single-select labelled `label`. */
async function selectOption(page, scope, label, option) {
    const toggle = scope.getByRole('combobox', { name: labelRe(label) })
        .or(controlGroup(scope, label).locator('button, [role="combobox"]'))
        .first();
    await toggle.click();
    await page.getByRole('option', { name: option, exact: true }).click();
}

/** Fill several text controls: `values` maps visible label -> value. */
async function fillForm(scope, values) {
    for (const [label, value] of Object.entries(values)) await fillText(scope, label, value);
}

/** Press Save on a Configuration tab and wait for the POST it makes to return.
 *  Returns the HTTP status (UCC answers a rejected value with 4xx/5xx). */
async function saveConfigTab(page) {
    const resp = page.waitForResponse((r) => r.request().method() === 'POST' && /\/servicesNS\//.test(r.url()), {
        timeout: 30000,
    });
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    return (await resp).status();
}

// ---------------------------------------------------------------------------
// Inputs page
// ---------------------------------------------------------------------------

const dialog = (page) => page.getByRole('dialog');

/** Open the add dialog for an input service. With one service UCC shows a
 *  plain "Create New Input" button; with several, a menu of service titles. */
async function openCreateInput(page, serviceTitle) {
    await page.getByRole('button', { name: 'Create New Input' }).click();
    if (!(await dialog(page).isVisible().catch(() => false))) {
        const item = page.getByRole('menuitem', { name: serviceTitle }).first();
        if (await item.isVisible().catch(() => false)) await item.click();
    }
    await expect(dialog(page)).toBeVisible();
    return dialog(page);
}

/** Press the dialog's submit button (Add / Update / Save) and wait for it to
 *  close. If it stays open the form error UCC shows is in the failure message. */
async function submitDialog(page, buttonName) {
    const d = dialog(page);
    await d.getByRole('button', { name: buttonName, exact: true }).click();
    try {
        await expect(d).toBeHidden({ timeout: 30000 });
    } catch (e) {
        const text = (await d.innerText().catch(() => '')).slice(0, 800);
        throw new Error(`dialog did not close after "${buttonName}"; it shows:\n${text}`);
    }
}

/** The table row whose cell text is exactly `name`. */
function inputRow(page, name) {
    return page.locator('tr').filter({ has: page.getByRole('cell', { name, exact: true }) });
}

async function rowAction(page, name, action) {
    await inputRow(page, name).getByRole('button', { name: action, exact: true }).click();
}

/** Flip the row's Active/Inactive switch and wait for it to settle. */
async function toggleRow(page, name) {
    const sw = inputRow(page, name).getByRole('switch');
    const before = await sw.getAttribute('aria-checked');
    await sw.click();
    await expect(sw).not.toHaveAttribute('aria-checked', before, { timeout: 30000 });
    await expect(sw).toBeEnabled({ timeout: 30000 });
}

// ---------------------------------------------------------------------------
// REST: read back what the UI wrote, by a route the UI never uses
// ---------------------------------------------------------------------------

async function restClient() {
    const api = await request.newContext({
        baseURL: MGMT_URL,
        ignoreHTTPSErrors: true,
        httpCredentials: { username: USER, password: PASS, send: 'always' },
    });
    const q = (s) => encodeURIComponent(s);
    const client = {
        api,
        async get(path, params = {}) {
            const r = await api.get(path, { params: { output_mode: 'json', ...params } });
            return { status: r.status(), body: await r.text() };
        },
        async post(path, form) {
            const r = await api.post(path, { form: { output_mode: 'json', ...form } });
            return { status: r.status(), body: await r.text() };
        },
        async del(path) {
            const r = await api.delete(path, { params: { output_mode: 'json' } });
            return { status: r.status(), body: await r.text() };
        },
        /** Content of [stanza] in <conf>.conf as splunkd merges it, or null. */
        async confStanza(app, conf, stanza) {
            const r = await client.get(`/servicesNS/nobody/${app}/configs/conf-${conf}/${q(stanza)}`);
            if (r.status === 404) return null;
            expect(r.status, `GET conf-${conf}/${stanza}: ${r.body.slice(0, 300)}`).toBe(200);
            return JSON.parse(r.body).entry[0].content;
        },
        /** Write keys into [stanza] through the raw conf endpoint (setup/restore). */
        async setConf(app, conf, stanza, values) {
            const r = await client.post(`/servicesNS/nobody/${app}/configs/conf-${conf}/${q(stanza)}`, values);
            expect(r.status, `POST conf-${conf}/${stanza}: ${r.body.slice(0, 300)}`).toBe(200);
        },
        /** [scheme://name] in inputs.conf, or null once it is gone. */
        inputStanza(app, scheme, name) {
            return client.confStanza(app, 'inputs', `${scheme}://${name}`);
        },
        /** splunkd's effective view of an input (data/inputs), which applies
         *  the scheme-level [<scheme>] stanza that configs/conf-inputs does not:
         *  this is the disabled state ExecProcessor honours. */
        async effectiveInput(app, scheme, name) {
            const r = await client.get(`/servicesNS/nobody/${app}/data/inputs/${q(scheme)}/${q(name)}`);
            if (r.status === 404) return null;
            expect(r.status, `GET data/inputs/${scheme}/${name}: ${r.body.slice(0, 300)}`).toBe(200);
            return JSON.parse(r.body).entry[0].content;
        },
        /** Remove an input stanza if a failed test left it behind. */
        async deleteInput(app, scheme, name) {
            await client.del(`/servicesNS/nobody/${app}/configs/conf-inputs/${q(`${scheme}://${name}`)}`);
        },
        /** Blocking search; returns the result rows. */
        async search(spl, earliest = '-15m') {
            const r = await api.post('/services/search/jobs/export', {
                form: { search: spl.trim().startsWith('|') ? spl : `search ${spl}`, output_mode: 'json',
                    earliest_time: String(earliest), latest_time: 'now' },
            });
            expect(r.ok(), `search failed (${r.status()}): ${spl}`).toBeTruthy();
            return (await r.text()).split('\n').filter((l) => l.trim()).map((l) => JSON.parse(l))
                .filter((o) => o.result && !o.preview).map((o) => o.result);
        },
        dispose: () => api.dispose(),
    };
    return client;
}

/** Splunk writes booleans as 1/0, true/false or t/f depending on the writer. */
const isTrue = (v) => ['1', 'true', 't', 'yes', 'y'].includes(String(v).toLowerCase());

/** Poll a conf stanza until `predicate(content)` holds; fail with the last seen
 *  content. Saves are synchronous in splunkd, but this absorbs the UI's
 *  debounce and the conf reload. */
async function expectConf(read, predicate, what, timeout = 20000) {
    // A plain loop rather than expect.poll: the failure message must show the
    // LAST content read, which a message built before polling cannot.
    let last;
    let error;
    const deadline = Date.now() + timeout;
    for (;;) {
        try {
            last = await read();
            error = undefined;
            if (predicate(last)) return last;
        } catch (e) {
            error = e;
        }
        if (Date.now() > deadline) break;
        await new Promise((r) => setTimeout(r, 500));
    }
    expect(false, `${what}; last seen: ${error ? `error ${error.message}` : JSON.stringify(last)}`).toBe(true);
    return last;
}

/** True if `secret` is anywhere in the page: the HTML, or the value of any
 *  input or textarea (page.content() does not show typed or prefilled values). */
async function domHolds(page, secret) {
    const values = await page.locator('input, textarea').evaluateAll((els) => els.map((e) => e.value));
    const html = await page.content();
    return values.some((v) => String(v).includes(secret)) || html.includes(secret);
}

/** The storage/passwords entries UCC keeps for [stanza] of <conf>.conf. UCC
 *  (solnlib CredentialManager) stores every encrypted field of a stanza as one
 *  JSON object under realm __REST_CREDENTIAL__#<app>#configs/conf-<conf>,
 *  split into chunks named <stanza>``splunk_cred_sep``<n>, the last of which
 *  holds only solnlib's end mark. Read from splunkd's storage/passwords endpoint, which
 *  the UCC pages never call. Returns the chunks in order. */
const CRED_SEP = '``splunk_cred_sep``';
// solnlib CredentialManager.END_MARK: the last chunk holds only this.
const CRED_END = '``splunk_cred_sep``S``splunk_cred_sep``P``splunk_cred_sep``L``splunk_cred_sep``'
    + 'U``splunk_cred_sep``N``splunk_cred_sep``K``splunk_cred_sep``';
async function credentialEntries(rest, app, conf, stanza, realms) {
    const r = await rest.get(`/servicesNS/nobody/${app}/storage/passwords`, { count: '0' });
    expect(r.status, `GET storage/passwords: ${r.status}`).toBe(200);
    // An input's secrets may sit under another endpoint than configs/conf-<conf>;
    // pass `realms` to accept those as well.
    const want = [`__REST_CREDENTIAL__#${app}#configs/conf-${conf}`, ...(realms || [])];
    const chunk = (u) => (u === stanza ? 0 : (String(u).startsWith(stanza + CRED_SEP)
        ? Number(String(u).slice(stanza.length + CRED_SEP.length)) : NaN));
    return JSON.parse(r.body).entry.map((e) => e.content)
        .filter((c) => want.includes(c.realm) && Number.isInteger(chunk(c.username)))
        .sort((x, y) => chunk(x.username) - chunk(y.username));
}

/** The JSON object solnlib stored across `entries` (from credentialEntries),
 *  or null if the chunks do not reassemble into one. */
function parseCredential(entries) {
    const parts = entries.map((c) => String(c.clear_password));
    if (parts.length && parts[parts.length - 1] === CRED_END) parts.pop();
    try { return JSON.parse(parts.join('')); } catch (e) { return null; }
}

/** The stored credential object for [stanza], or null. */
async function readCredential(rest, app, conf, stanza, realms) {
    return parseCredential(await credentialEntries(rest, app, conf, stanza, realms));
}

/** Realm/username pairs in storage/passwords for this app (never passwords),
 *  for failure messages. */
async function credentialIndex(rest, app) {
    const r = await rest.get(`/servicesNS/nobody/${app}/storage/passwords`, { count: '0' });
    return r.status === 200
        ? JSON.parse(r.body).entry.map((e) => `${e.content.realm} / ${e.content.username}`).join('; ') : `HTTP ${r.status}`;
}

/** The secret saved in `field` of [stanza] is held in storage/passwords. */
async function expectSecretStored(rest, { app, conf, stanza, field, secret, realms }) {
    let entries = [];
    await expect.poll(async () => {
        entries = await credentialEntries(rest, app, conf, stanza, realms);
        return entries.length > 0;
    }, { message: `storage/passwords has no credential for ${conf} [${stanza}]; it holds: ${await credentialIndex(rest, app)}`,
        timeout: 20000 }).toBe(true);
    const stored = parseCredential(entries);
    // Compare without printing: the message names the field, never the value.
    expect(!!stored && stored[field] === secret,
        `storage/passwords credential for ${conf} [${stanza}] does not hold the saved ${field}` +
        ` (${entries.length} chunk(s), JSON ${stored ? 'with keys ' + Object.keys(stored).join(',') : 'unparsable'})`).toBe(true);
}

/** No credential is left in storage/passwords for [stanza] (after a delete). */
async function expectSecretGone(rest, { app, conf, stanza, realms }) {
    await expect.poll(async () => (await credentialEntries(rest, app, conf, stanza, realms)).length,
        { message: `storage/passwords still holds a credential for ${conf} [${stanza}]`, timeout: 20000 }).toBe(0);
}

/**
 * An encrypted field's secret must not come back, and must be where UCC keeps
 * secrets. After saving `secret` into the field labelled `label`:
 *  - the page as it stands does not hold the plain secret anywhere in the DOM
 *    (input values included, which page.content() does not show);
 *  - the add-on's own REST handler returns the mask, not the secret;
 *  - the conf file itself does not hold it in plain text (UCC leaves the mask
 *    in the conf), read from the raw configs/conf-* endpoint;
 *  - storage/passwords holds it (expectSecretStored).
 * `handlerPath` is e.g. /servicesNS/nobody/<app>/<restRoot>_account/<name>.
 * Reload the page and call domHolds() again to cover "after reload".
 */
async function expectSecretNotEchoed(page, rest, { app, conf, stanza, handlerPath, field, secret, realms, credStanza, dom = true }) {
    // Every check runs and every failure is reported together, so a defect
    // shows each place the secret leaked, not just the first one checked.
    // dom: false on a settings tab straight after Save, where the field still
    // holds what was typed; check the DOM after a reload instead.
    const failures = [];
    if (dom && await domHolds(page, secret)) failures.push(`secret for ${field} is in the DOM`);
    const viaHandler = await rest.get(handlerPath);
    if (viaHandler.status !== 200) {
        failures.push(`GET ${handlerPath}: ${viaHandler.status}`);
    } else {
        if (viaHandler.body.includes(secret)) failures.push(`${handlerPath} echoes the secret for ${field}`);
        const content = JSON.parse(viaHandler.body).entry[0].content;
        if (content[field] !== MASK) failures.push(`${handlerPath} does not mask ${field}`);
    }
    const raw = await rest.confStanza(app, conf, stanza);
    if (!raw || !raw[field]) failures.push(`${conf}.conf [${stanza}] has no ${field}`);
    else if (String(raw[field]).includes(secret)) failures.push(`${conf}.conf [${stanza}] stores ${field} in plain text`);
    let stored = null;
    let entries = [];
    const deadline = Date.now() + 20000;
    do {
        entries = await credentialEntries(rest, app, conf, credStanza || stanza, realms);
        stored = parseCredential(entries);
        if (stored && stored[field] === secret) break;
        await page.waitForTimeout(1000);
    } while (Date.now() < deadline);
    if (!entries.length) {
        failures.push(`storage/passwords has no credential for ${conf} [${credStanza || stanza}]; it holds: ${await credentialIndex(rest, app)}`);
    } else if (!stored || stored[field] !== secret) {
        failures.push(`storage/passwords credential for ${conf} [${credStanza || stanza}] does not hold the saved ${field}`
            + ` (${entries.length} chunk(s), JSON ${stored ? 'with keys ' + Object.keys(stored).join(',') : 'unparsable'})`);
    }
    // Soft: the test still fails, but walks on, so later leak checks (reload, edit
    // dialog) are reported too.
    expect.soft(failures, `secret checks for ${field}:\n${failures.join('\n')}`).toEqual([]);
}

/** Tick or untick the checkbox labelled `label` (react-ui Switch, checkbox
 *  appearance: role checkbox or switch with aria-checked). */
async function setCheckbox(scope, label, checked) {
    const box = controlGroup(scope, label).locator('[role="checkbox"], [role="switch"], input[type="checkbox"]').first();
    const now = async () => {
        const aria = await box.getAttribute('aria-checked');
        return aria !== null ? aria === 'true' : box.isChecked();
    };
    if ((await now()) !== checked) await box.click();
    await expect.poll(now, { message: `${label} did not become ${checked}` }).toBe(checked);
}

module.exports = {
    MGMT_URL, USER, PASS, MASK,
    login, watchAppErrors, gotoUccPage, openTab,
    control, fillText, readText, selectOption, fillForm, saveConfigTab,
    dialog, openCreateInput, submitDialog, inputRow, rowAction, toggleRow,
    restClient, isTrue, expectConf, expectSecretNotEchoed, controlGroup, labelRe,
    loadGlobalConfig, configTab, tabTitle,
    domHolds, credentialEntries, readCredential, expectSecretStored, expectSecretGone, setCheckbox,
};

/*
 * Adopting this in another UCC add-on
 * -----------------------------------
 * 1. Copy e2e/ucc/ (this file), e2e/package.json, e2e/package-lock.json and
 *    e2e/playwright.config.js into the TA.
 * 2. Write e2e/<ta>-configuration.spec.js and e2e/<ta>-inputs.spec.js using the
 *    labels and conf names from that TA's globalConfig.json (read the JSON in
 *    the spec for labels and validator messages: it is the spec of the UI, not
 *    the code under test).
 * 3. Settings tabs save to <restRoot lower-cased>_settings.conf; account tabs to
 *    <restRoot lower-cased>_account.conf; inputs to inputs.conf as
 *    [<service name>://<input name>].
 * 4. Add the Playwright steps from TA-airspace-watch's integration-test job
 *    after the pytest step, so the browser suite shares the Splunk the
 *    integration legs already start (10.0 and 10.6.0).
 * 5. Break one field mapping and one handler on a mutant-* branch and watch the
 *    specs go red before trusting them.
 */
