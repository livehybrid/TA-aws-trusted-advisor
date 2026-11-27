/*
 * Inputs page of TA-aws-trusted-advisor, driven in a browser on a real Splunk: create,
 * disable, enable, edit and delete an input, each step read back from
 * splunkd's raw configs/conf-inputs endpoint. The AWS secret key is checked
 * never to come back (DOM, handler, plain inputs.conf) and to be held in
 * storage/passwords.
 *
 * Keys are dummies and the app's endpoint override (ta_aws_trusted_advisor_endpoints,
 * the seam the pytest suite uses) points support and sts at the compose mock,
 * so an enabled input never reaches AWS.
 */
const path = require('path');
const { test, expect } = require('@playwright/test');
const ucc = require('./ucc/ucc-helpers');

const GC = ucc.loadGlobalConfig(path.join(__dirname, '..'));
const APP = GC.meta.name;
const PAGE_TITLE = GC.pages.inputs.title;
const SERVICE = 'aws_trusted_advisor';
const MOCK_FROM_SPLUNK = process.env.MOCK_AWS_URL_FROM_SPLUNK || 'http://mockaws:8080';

// Visible label -> inputs.conf key, written out here.
const FIELDS = {
    aws_trusted_advisor: { name: 'Name', interval: 'Interval', index: 'Index', aws_access_key: 'AWS Access Key',
        aws_secret_key: 'AWS Secret Key', role_arn: 'Role ARN' },
};
const VALUES = {
    aws_trusted_advisor: {
        create: { interval: '3600', index: 'main', aws_access_key: 'AKIAE2EDUMMY00000001' },
        edit: { interval: '7200', role_arn: 'arn:aws:iam::111111111111:role/e2e-dummy' },
    },
};

let rest;
test.beforeAll(async () => {
    rest = await ucc.restClient();
    for (const svc of ['support', 'sts']) await rest.setConf(APP, 'ta_aws_trusted_advisor_endpoints', svc, { endpoint_url: MOCK_FROM_SPLUNK })
        .catch(async () => {
            const r = await rest.post(`/servicesNS/nobody/${APP}/configs/conf-ta_aws_trusted_advisor_endpoints`,
                { name: svc, endpoint_url: MOCK_FROM_SPLUNK });
            expect([200, 201], `endpoint override [${svc}]: ${r.status}`).toContain(r.status);
        });
    for (const svc of ['support', 'sts']) {
        expect((await rest.confStanza(APP, 'ta_aws_trusted_advisor_endpoints', svc)).endpoint_url, `[${svc}] override`)
            .toBe(MOCK_FROM_SPLUNK);
    }
});
test.afterAll(async () => rest.dispose());
test.beforeEach(async ({ page }) => ucc.login(page));

test('every input service has test values and labels', () => {
    const services = GC.pages.inputs.services.map((s) => s.name).sort();
    expect(services).toEqual(Object.keys(VALUES).sort());
    expect(services).toEqual(Object.keys(FIELDS).sort());
});

async function fillTexts(d, label, values) {
    for (const [f, v] of Object.entries(values)) {
        await ucc.fillText(d, label(f), v);
        await ucc.control(d, label(f)).press('Tab');
    }
}

const label = (key) => FIELDS[SERVICE][key];
const stanza = (name) => rest.inputStanza(APP, SERVICE, name);
const service = GC.pages.inputs.services.find((s) => s.name === SERVICE);

test(`${SERVICE}: create, disable, enable, edit and delete; the secret key never comes back`, async ({ page }) => {
    const errs = ucc.watchAppErrors(page, APP);
    const name = `e2e_${SERVICE}_${Date.now().toString(36)}`;
    const secret = `e2eDummySecret${Math.random().toString(36).slice(2)}`;
    const { create, edit } = VALUES[SERVICE];
    const check = { app: APP, conf: 'inputs', stanza: `${SERVICE}://${name}`, credStanza: name,
        realms: [`__REST_CREDENTIAL__#${APP}#data/inputs/${SERVICE}`], field: 'aws_secret_key', secret,
        handlerPath: `/servicesNS/nobody/${APP}/${GC.meta.restRoot}_${SERVICE}/${name}` };
    try {
        await ucc.gotoUccPage(page, APP, 'inputs', PAGE_TITLE);

        const d = await ucc.openCreateInput(page, service.title);
        await fillTexts(d, label, { name, ...create, aws_secret_key: secret });
        await ucc.submitDialog(page, 'Add');
        await expect(ucc.inputRow(page, name)).toBeVisible();
        const made = await ucc.expectConf(() => stanza(name), (c) => c !== null, `[${SERVICE}://${name}] created`);
        for (const [f, v] of Object.entries(create)) expect(made[f], `created ${f}`).toBe(v);
        await ucc.expectSecretNotEchoed(page, rest, check);

        // The Status switch must show what splunkd will run. data/inputs applies
        // the scheme-level [<service>] stanza in default/inputs.conf, which
        // configs/conf-inputs does not, so the effective state is read there.
        const effective = async () => ucc.isTrue((await rest.effectiveInput(APP, SERVICE, name)).disabled);
        const startsDisabled = await effective();
        await expect(ucc.inputRow(page, name).getByRole('switch'), 'the Status switch agrees with splunkd')
            .toHaveAttribute('aria-checked', String(!startsDisabled));
        for (const want of [!startsDisabled, startsDisabled]) {
            await ucc.toggleRow(page, name);
            await ucc.expectConf(effective, (d) => d === want, `splunkd disabled=${want} after the switch`);
            await ucc.expectConf(() => stanza(name), (c) => c && ucc.isTrue(c.disabled) === want,
                `inputs.conf disabled=${want} after the switch`);
        }

        await page.reload();
        await expect(ucc.inputRow(page, name)).toBeVisible();
        expect.soft(await ucc.domHolds(page, secret), 'secret key in the DOM after reload').toBe(false);

        await ucc.rowAction(page, name, 'Edit');
        const e = ucc.dialog(page);
        await expect(e).toBeVisible();
        for (const [f, v] of Object.entries(create)) expect(await ucc.readText(e, label(f)), `edit shows ${f}`).toBe(v);
        expect.soft(await ucc.domHolds(page, secret), 'secret key in the edit dialog').toBe(false);
        await fillTexts(e, label, edit);
        await ucc.submitDialog(page, 'Update');
        const after = await ucc.expectConf(() => stanza(name),
            (c) => c && Object.entries(edit).every(([f, v]) => c[f] === v), 'edited values saved');
        for (const [f, v] of Object.entries({ ...create, ...edit })) expect(after[f], `after edit ${f}`).toBe(v);
        // The secret survives an edit that did not touch it.
        await ucc.expectSecretNotEchoed(page, rest, check);

        await ucc.rowAction(page, name, 'Delete');
        await ucc.submitDialog(page, 'Delete');
        await expect(ucc.inputRow(page, name)).toHaveCount(0);
        await ucc.expectConf(() => stanza(name), (c) => c === null, `[${SERVICE}://${name}] removed`);
        await ucc.expectSecretGone(rest, { app: APP, conf: 'inputs', stanza: name, realms: check.realms });
        errs.assertNone();
    } finally {
        await rest.deleteInput(APP, SERVICE, name);
    }
});

test(`${SERVICE}: an invalid name or interval is refused and nothing is written`, async ({ page }) => {
    const bad = '9_starts_with_a_digit';
    await ucc.gotoUccPage(page, APP, 'inputs', PAGE_TITLE);
    const d = await ucc.openCreateInput(page, service.title);
    await fillTexts(d, label, { name: bad, ...VALUES[SERVICE].create });
    await d.getByRole('button', { name: 'Add', exact: true }).click();
    const nameMsg = service.entity.find((x) => x.field === 'name').validators[0].errorMsg;
    await expect(d.getByText(nameMsg).first()).toBeVisible();
    const good = `e2e_badinterval_${Date.now().toString(36)}`;
    await fillTexts(d, label, { name: good, interval: 'hourly' });
    await d.getByRole('button', { name: 'Add', exact: true }).click();
    const intMsg = service.entity.find((x) => x.field === 'interval').validators[0].errorMsg;
    await expect(d.getByText(intMsg).first()).toBeVisible();
    await page.waitForTimeout(3000);
    expect(await stanza(bad)).toBeNull();
    expect(await stanza(good)).toBeNull();
    await d.getByRole('button', { name: 'Cancel', exact: true }).click();
});
