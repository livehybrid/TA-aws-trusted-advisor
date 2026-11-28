"""
`| getchecks` signs its AWS calls with the input's own access key, whatever
else the add-on (or the search context) has in storage/passwords.

It used to take the first credential it could see in the search's app and
json.loads it as the AWS keys. The Proxy tab's password is stored in the same
app, under a realm that lists before the inputs' one, so saving a proxy
password broke `| getchecks`; so did any other secret stored in the app, and
running it from the Search app (as the README says to) found nothing at all.

Scenarios, one after the other, each running `| getchecks` once:
  baseline      only the input's keys stored, run in the add-on's app
  search_app    the same, run from the Search app
  other_secret  another secret stored in the add-on's app, sorting first
  proxy_secret  a proxy password saved on the Proxy tab (proxy left off)

Which key signed the call is read from the mock AWS's request log, a record
getchecks never consults. The input is created with a yearly cron schedule and
disabled straight away, so the only DescribeTrustedAdvisorChecks calls signed
with its key are getchecks' own.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

import pytest

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
INPUTS = f"{NS}/data/inputs/aws_trusted_advisor"
SETTINGS = f"{NS}/TA_aws_trusted_advisor_settings/proxy"
CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
PASSWORDS = f"{NS}/storage/passwords"
INPUT_NAME = "aaa_getchecks_keys"
INPUT_KEY = "AKIAGETCHECKSKEY0001"
# Sorts before every __REST_CREDENTIAL__ realm, as a position-based pick would meet it first.
DECOY_REALM, DECOY_USER = "0-e2e-decoy", "decoy"
PROXY_SECRET = "decoy-proxy-p@ss:word"
MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
MOCK_URL_FROM_SPLUNK = os.environ.get("MOCK_AWS_URL_FROM_SPLUNK", "http://mockaws:8080")
SCENARIOS = ("baseline", "search_app", "other_secret", "proxy_secret")


def _requests():
    with urllib.request.urlopen(MOCK_URL + "/_mock/requests", timeout=30) as r:
        return json.loads(r.read())


def _getchecks(splunk, app):
    calls_from = len(_requests())
    st, body = splunk.request("POST", f"/servicesNS/admin/{app}/search/jobs/oneshot",
                              data={"search": "| getchecks", "output_mode": "json", "count": 0})
    time.sleep(3)
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = {}
    return {"status": st, "rows": parsed.get("results", []), "messages": parsed.get("messages", body[:600]),
            "calls": [r for r in _requests()[calls_from:] if r.get("operation") == "DescribeTrustedAdvisorChecks"]}


def _save_proxy(splunk, password):
    st, body = splunk.request("POST", SETTINGS, params={"output_mode": "json"},
                              data={"proxy_enabled": "0", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
                                    "proxy_username": "", "proxy_password": password, "proxy_rdns": "0"})
    assert st == 200, f"save Proxy tab -> {st}: {body[:400]}"


@pytest.fixture(scope="module")
def runs(splunk):
    for stanza in ("support", "sts"):
        st, body = splunk.request("POST", CONF, data={"name": stanza, "endpoint_url": MOCK_URL_FROM_SPLUNK})
        if st == 409:
            st, body = splunk.request("POST", f"{CONF}/{stanza}", data={"endpoint_url": MOCK_URL_FROM_SPLUNK})
        assert st in (200, 201), f"endpoint override [{stanza}] -> {st}: {body[:300]}"
    st, body = splunk.request("POST", HANDLER, data={
        "name": INPUT_NAME, "interval": "0 0 1 1 *", "index": "main",
        "aws_access_key": INPUT_KEY, "aws_secret_key": "mock-secret-not-real"})
    assert st in (200, 201), f"create {INPUT_NAME} -> {st}: {body[:400]}"
    splunk.request("POST", f"{INPUTS}/{INPUT_NAME}/disable")
    decoy = urllib.parse.quote(f"{DECOY_REALM}:{DECOY_USER}:", safe="")
    out = {}
    try:
        out["baseline"] = _getchecks(splunk, APP)
        out["search_app"] = _getchecks(splunk, "search")

        st, body = splunk.request("POST", PASSWORDS, data={"name": DECOY_USER, "realm": DECOY_REALM,
                                                            "password": "not-json, not aws keys"})
        assert st in (200, 201), f"store decoy secret -> {st}: {body[:300]}"
        try:
            out["other_secret"] = _getchecks(splunk, APP)
        finally:
            splunk.request("DELETE", f"{PASSWORDS}/{decoy}")

        _save_proxy(splunk, PROXY_SECRET)
        try:
            out["realms_with_proxy_secret"] = {e["content"].get("realm") for e in splunk.entries(PASSWORDS)}
            out["proxy_secret"] = _getchecks(splunk, APP)
        finally:
            _save_proxy(splunk, "")
        yield out
    finally:
        splunk.request("DELETE", f"{HANDLER}/{INPUT_NAME}")
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{CONF}/{stanza}")


def test_proxy_password_was_really_stored(runs):
    # Without this the proxy scenario could pass with no second credential present:
    # the realm UCC stores the Proxy tab's password under, listed while it was saved.
    realms = runs["realms_with_proxy_secret"]
    assert any(r and r.endswith("#configs/conf-ta_aws_trusted_advisor_settings") for r in realms), realms


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_getchecks_signs_with_the_input_key(runs, scenario):
    g = runs[scenario]
    diag = f"\nstatus {g['status']} messages {g['messages']}\ncalls {[(c.get('access_key_id'), c.get('error')) for c in g['calls']]}"
    assert g["status"] == 200 and g["rows"], f"| getchecks returned no rows in {scenario}" + diag
    assert g["calls"], f"| getchecks never reached the mock in {scenario}" + diag
    keys = {c.get("access_key_id") for c in g["calls"]}
    assert keys == {INPUT_KEY}, f"| getchecks signed with {keys}, not the input's key {INPUT_KEY}" + diag
