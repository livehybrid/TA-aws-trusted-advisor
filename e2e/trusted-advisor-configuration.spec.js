/*
 * Configuration page of TA-aws-trusted-advisor, driven in a browser on a real Splunk.
 * Each save is read back from splunkd's raw configs/conf-* endpoint and the
 * proxy password from storage/passwords, neither of which the page or the
 * add-on's REST handlers read. All values are dummies.
 */
const path = require('path');
const { test, expect } = require('@playwright/test');
const ucc = require('./ucc/ucc-helpers');

const GC = ucc.loadGlobalConfig(path.join(__dirname, '..'));
const APP = GC.meta.name;
const ROOT = GC.meta.restRoot.toLowerCase();
const SETTINGS_CONF = `${ROOT}_settings`;
const PAGE_TITLE = GC.pages.configuration.title;
const tab = (name) => ucc.configTab(GC, name);
const title = ucc.tabTitle;

// Visible label -> conf key, written out rather than looked up by field.
const PROXY = { proxy_enabled: 'Enable', proxy_type: 'Proxy Type', proxy_url: 'Host', proxy_port: 'Port',
    proxy_username: 'Username', proxy_password: 'Password', proxy_rdns: 'Remote DNS resolution' };
const LOGGING = { loglevel: 'Log level' };

let rest;
const saved = {};

test.beforeAll(async () => {
    rest = await ucc.restClient();
    for (const s of ['logging', 'proxy']) saved[s] = await rest.confStanza(APP, SETTINGS_CONF, s);
});

test.afterAll(async () => {
    await rest.setConf(APP, SETTINGS_CONF, 'logging', { loglevel: (saved.logging && saved.logging.loglevel) || 'INFO' });
    // Never leave a proxy on: the collector would send the mock's traffic to it.
    await rest.setConf(APP, SETTINGS_CONF, 'proxy', { proxy_enabled: '0', proxy_url: '', proxy_port: '',
        proxy_username: '', proxy_rdns: '0' });
    await rest.dispose();
});

test.beforeEach(async ({ page }) => {
    await ucc.login(page);
});

test('Configuration page loads every tab without app errors', async ({ page }) => {
    const errs = ucc.watchAppErrors(page, APP);
    await ucc.gotoUccPage(page, APP, 'configuration', PAGE_TITLE);
    for (const t of GC.pages.configuration.tabs) {
        await ucc.openTab(page, title(t));
        if (t.type === 'loggingTab') await expect(ucc.controlGroup(page, LOGGING.loglevel)).toBeVisible();
        for (const e of t.entity || []) await expect(ucc.controlGroup(page, e.label)).toBeVisible();
    }
    errs.assertNone();
});

test('Logging tab saves the chosen level and it survives a reload', async ({ page }) => {
    await ucc.gotoUccPage(page, APP, 'configuration', PAGE_TITLE);
    await ucc.openTab(page, 'Logging');
    const target = saved.logging && saved.logging.loglevel === 'DEBUG' ? 'WARNING' : 'DEBUG';
    await ucc.selectOption(page, page, LOGGING.loglevel, target);
    expect(await ucc.saveConfigTab(page)).toBe(200);
    await ucc.expectConf(() => rest.confStanza(APP, SETTINGS_CONF, 'logging'),
        (c) => c && c.loglevel === target, `[logging] loglevel=${target} after Save`);
    await page.reload();
    await ucc.openTab(page, 'Logging');
    await expect(ucc.controlGroup(page, LOGGING.loglevel)).toContainText(target);
});

test('Proxy tab saves, survives a reload, and the password never comes back', async ({ page }) => {
    const secret = `e2e-dummy-proxy-${Math.random().toString(36).slice(2)}`;
    const want = { proxy_url: 'proxy.e2e.example', proxy_port: '3128', proxy_username: 'e2euser' };
    try {
        await ucc.gotoUccPage(page, APP, 'configuration', PAGE_TITLE);
        await ucc.openTab(page, title(tab('proxy')));
        await ucc.setCheckbox(page, PROXY.proxy_enabled, true);
        await ucc.selectOption(page, page, PROXY.proxy_type, 'socks5');
        for (const [f, v] of Object.entries(want)) await ucc.fillText(page, PROXY[f], v);
        await ucc.fillText(page, PROXY.proxy_password, secret);
        await ucc.setCheckbox(page, PROXY.proxy_rdns, true);
        expect(await ucc.saveConfigTab(page)).toBe(200);

        const got = await ucc.expectConf(() => rest.confStanza(APP, SETTINGS_CONF, 'proxy'),
            (c) => c && c.proxy_url === want.proxy_url, '[proxy] after Save');
        for (const [f, v] of Object.entries(want)) expect(got[f], f).toBe(v);
        expect(got.proxy_type, 'proxy_type').toBe('socks5');
        expect(ucc.isTrue(got.proxy_enabled), 'proxy_enabled').toBe(true);
        expect(ucc.isTrue(got.proxy_rdns), 'proxy_rdns').toBe(true);
        await ucc.expectSecretNotEchoed(page, rest, { app: APP, conf: SETTINGS_CONF, stanza: 'proxy',
            field: 'proxy_password', secret, dom: false, handlerPath: `/servicesNS/nobody/${APP}/${GC.meta.restRoot}_settings/proxy` });

        await page.reload();
        await ucc.openTab(page, title(tab('proxy')));
        for (const [f, v] of Object.entries(want)) expect(await ucc.readText(page, PROXY[f]), f).toBe(v);
        expect.soft(await ucc.domHolds(page, secret), 'proxy password in the DOM after reload').toBe(false);
    } finally {
        await rest.setConf(APP, SETTINGS_CONF, 'proxy', { proxy_enabled: '0' });
    }
});

test('Proxy tab refuses an enabled proxy without a host and writes nothing', async ({ page }) => {
    await rest.setConf(APP, SETTINGS_CONF, 'proxy', { proxy_enabled: '0', proxy_url: '', proxy_port: '' });
    const before = await rest.confStanza(APP, SETTINGS_CONF, 'proxy');
    await ucc.gotoUccPage(page, APP, 'configuration', PAGE_TITLE);
    await ucc.openTab(page, title(tab('proxy')));
    await ucc.setCheckbox(page, PROXY.proxy_enabled, true);
    await ucc.fillText(page, PROXY.proxy_port, '3128');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    // The message is the tab's saveValidator in globalConfig.json.
    await expect(page.getByText('Proxy Host can not be empty').first()).toBeVisible();
    await page.waitForTimeout(3000);
    expect(await rest.confStanza(APP, SETTINGS_CONF, 'proxy')).toEqual(before);
});
