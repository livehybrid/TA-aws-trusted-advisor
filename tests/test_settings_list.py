"""
Guard: the UCC settings endpoint lists every settings stanza in one request.

GET /servicesNS/nobody/<app>/<restRoot>_settings (no stanza name) is how a
script or API client reads the whole Configuration page at once. splunktaucclib
(up to at least 8.2.0) answers it with 500 wrapping "REST Error [404] ...
name=None"; bin/ucc_settings_list_handler.py fixes it. The UI's per-stanza GETs
were never affected.

Expected stanza names come from the repo's globalConfig.json, read here as JSON
(the handler only sees the generated Python models). Stanzas a fresh install
lacks are created through splunkd's raw configs/conf-* endpoint, which the UCC
handler does not serve, and removed again afterwards.
"""
from __future__ import annotations

import json
import pathlib
import urllib.parse
import uuid

import pytest

GC = json.loads((pathlib.Path(__file__).resolve().parent.parent / "globalConfig.json").read_text())
APP = GC["meta"]["name"]
REST_ROOT = GC["meta"]["restRoot"]
ENDPOINT = f"/servicesNS/nobody/{APP}/{REST_ROOT}_settings"
CONF = f"/servicesNS/nobody/{APP}/configs/conf-{REST_ROOT.lower()}_settings"
MASK = "******"


def _settings_tabs():
    tabs = []
    for tab in GC["pages"].get("configuration", {}).get("tabs", []):
        if "table" in tab:  # account-style tabs are their own endpoints
            continue
        name = tab.get("name") or ("logging" if tab.get("type") == "loggingTab" else None)
        assert name, f"settings tab without a name: {tab}"
        encrypted = [e["field"] for e in tab.get("entity", []) if e.get("encrypted")]
        tabs.append((name, encrypted))
    return tabs


TABS = _settings_tabs()
SECRET_FIELDS = [(name, field) for name, fields in TABS for field in fields]


def _q(name):
    return urllib.parse.quote(name, safe="")


@pytest.fixture(scope="module")
def stanzas(splunk):
    """Make sure every settings stanza exists; remove the ones this test made."""
    created = []
    for name, _ in TABS:
        status, _body = splunk.request("GET", f"{CONF}/{_q(name)}", params={"output_mode": "json"})
        if status == 404:
            status, body = splunk.request("POST", CONF, data={"name": name}, params={"output_mode": "json"})
            assert status in (200, 201), f"could not create [{name}]: {status} {body[:300]}"
            created.append(name)
        else:
            assert status == 200, f"GET conf stanza [{name}] -> {status}"
    yield [name for name, _ in TABS]
    for name in created:
        splunk.request("DELETE", f"{CONF}/{_q(name)}")


def _list(splunk, **params):
    params.setdefault("output_mode", "json")
    params.setdefault("count", "0")
    status, body = splunk.request("GET", ENDPOINT, params=params)
    return status, body


def test_settings_tabs_found_in_global_config():
    assert TABS, "globalConfig.json declares no settings tabs; this guard has nothing to check"


def test_settings_list_returns_every_stanza(splunk, stanzas):
    status, body = _list(splunk)
    assert status == 200, f"GET {ENDPOINT} -> {status}: {body[:400]}"
    names = sorted(e["name"] for e in json.loads(body)["entry"])
    assert names == sorted(stanzas)


def test_settings_list_agrees_with_per_stanza_reads(splunk, stanzas):
    status, body = _list(splunk)
    assert status == 200, f"GET {ENDPOINT} -> {status}: {body[:400]}"
    listed = {e["name"]: e["content"] for e in json.loads(body)["entry"]}
    for name in stanzas:
        one = splunk.get_json(f"{ENDPOINT}/{_q(name)}")["entry"]
        assert len(one) == 1, f"per-stanza GET of {name} returned {len(one)} entries"
        strip = lambda c: {k: v for k, v in c.items() if not k.startswith("eai:")}
        assert strip(listed[name]) == strip(one[0]["content"]), name


@pytest.mark.skipif(not SECRET_FIELDS, reason="no encrypted settings fields in globalConfig.json")
@pytest.mark.parametrize("stanza,field", SECRET_FIELDS)
def test_settings_list_masks_encrypted_fields(splunk, stanzas, stanza, field):
    secret = "guard" + uuid.uuid4().hex
    status, body = splunk.request(
        "POST", f"{ENDPOINT}/{_q(stanza)}", data={field: secret}, params={"output_mode": "json"}
    )
    assert status == 200, f"storing {stanza}.{field} -> {status}: {body[:300]}"
    try:
        # Positive control: the secret really is stored and readable in clear,
        # so a masked list is masking something.
        clear = splunk.get_json(f"{ENDPOINT}/{_q(stanza)}", **{"--cred--": "1"})
        assert clear["entry"][0]["content"].get(field) == secret

        status, body = _list(splunk)
        assert status == 200, f"GET {ENDPOINT} -> {status}: {body[:400]}"
        assert secret not in body, f"{stanza}.{field} in clear in the settings list"
        entry = {e["name"]: e["content"] for e in json.loads(body)["entry"]}[stanza]
        assert entry.get(field) == MASK
    finally:
        splunk.request("POST", f"{ENDPOINT}/{_q(stanza)}", data={field: ""}, params={"output_mode": "json"})
