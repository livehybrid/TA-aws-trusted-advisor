"""
`| getchecks` with an input that has a role: it uses the role's credentials or
stops with an error. It never falls back to the input's base keys.

The modular input already refused to (tests/test_mock_aws_role_failure.py).
getchecks used to log "Could not assume role" and carry on with the base keys,
collecting as a different principal. Its STS client also had no region, which
with an endpoint override raises NoRegionError before anything is sent, so in
that case it never even tried the role. getchecks now builds its client with
the input's own code (common.support_client).

Scenarios, one after the other, each with its own input (first in the
listing, yearly schedule, disabled at once so only getchecks calls AWS) and
access key:
  allowed  a role the mock grants
  denied   a role the mock refuses (role name mock-denied*: 403 AccessDenied)

Which key signed each call, and the region it was signed for, are read from
the mock AWS's request log, a record getchecks never consults.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

import pytest

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
INPUTS = f"{NS}/data/inputs/aws_trusted_advisor"
CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
MOCK_URL_FROM_SPLUNK = os.environ.get("MOCK_AWS_URL_FROM_SPLUNK", "http://mockaws:8080")
ALLOWED_ROLE = "arn:aws:iam::111111111111:role/mock-trusted-advisor-reader"
DENIED_ROLE = "arn:aws:iam::111111111111:role/mock-denied-getchecks"
SCENARIOS = {
    "allowed": ("AKIAGETCHECKSROLEOK1", ALLOWED_ROLE),
    "denied": ("AKIAGETCHECKSROLENO1", DENIED_ROLE),
}


def _requests():
    with urllib.request.urlopen(MOCK_URL + "/_mock/requests", timeout=30) as r:
        return json.loads(r.read())


def search_text(body):
    """Every message and result value of a oneshot response as one string,
    JSON-decoded (so the test does not depend on how splunkd escapes '/')."""
    try:
        doc = json.loads(body)
    except ValueError:
        return body
    parts = [m.get("text", "") for m in doc.get("messages", []) or []]
    parts += [str(v) for row in doc.get("results", []) or [] for v in row.values()]
    return "\n".join(parts) or body


def _run(splunk, name):
    key, role = SCENARIOS[name]
    input_name = f"aaa_gc_role_{name}"
    st, body = splunk.request("POST", HANDLER, data={
        "name": input_name, "interval": "0 0 1 1 *", "index": "main", "role_arn": role,
        "aws_access_key": key, "aws_secret_key": "mock-secret-not-real"})
    assert st in (200, 201), f"create {input_name} -> {st}: {body[:400]}"
    splunk.request("POST", f"{INPUTS}/{input_name}/disable")
    try:
        calls_from = len(_requests())
        st, body = splunk.request("POST", f"/servicesNS/admin/{APP}/search/jobs/oneshot",
                                  data={"search": "| getchecks", "output_mode": "json", "count": 0})
        time.sleep(3)
        reqs = _requests()[calls_from:]
    finally:
        splunk.request("DELETE", f"{HANDLER}/{input_name}")
    try:
        rows = [r for r in json.loads(body).get("results", []) if "ERROR" not in r]
    except ValueError:
        rows = []
    assumes = [r for r in reqs if r.get("operation") == "AssumeRole" and r.get("access_key_id") == key]
    issued = {a["issued"]["AccessKeyId"] for a in assumes if a.get("issued")}
    support = [r for r in reqs if r.get("operation") == "DescribeTrustedAdvisorChecks"]
    return {"status": st, "body": search_text(body)[:3000], "rows": rows, "assumes": assumes, "issued": issued,
            "support": support, "key": key}


@pytest.fixture(scope="module")
def runs(splunk):
    for stanza in ("support", "sts"):
        st, body = splunk.request("POST", CONF, data={"name": stanza, "endpoint_url": MOCK_URL_FROM_SPLUNK})
        if st == 409:
            st, body = splunk.request("POST", f"{CONF}/{stanza}", data={"endpoint_url": MOCK_URL_FROM_SPLUNK})
        assert st in (200, 201), f"endpoint override [{stanza}] -> {st}: {body[:300]}"
    try:
        yield {name: _run(splunk, name) for name in SCENARIOS}
    finally:
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{CONF}/{stanza}")


def _diag(r):
    return (f"\ngetchecks -> {r['status']}: {r['body'][:600]}"
            f"\nAssumeRole (key, region, error): {[(a.get('access_key_id'), a.get('signing_region'), a.get('error')) for a in r['assumes']]}"
            f"\nSupport calls (key, region, error): {[(s.get('access_key_id'), s.get('signing_region'), s.get('error')) for s in r['support']]}")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_getchecks_asks_sts_for_the_role_in_us_east_1(runs, name):
    # Also the positive control for the denied scenario: the role was really tried.
    r = runs[name]
    assert r["assumes"], "| getchecks never called AssumeRole with the input's key" + _diag(r)
    assert {a.get("signing_region") for a in r["assumes"]} == {"us-east-1"}, _diag(r)
    assert {a["params"].get("RoleArn") for a in r["assumes"]} == {SCENARIOS[name][1]}, _diag(r)


def test_getchecks_signs_with_the_roles_credentials(runs):
    r = runs["allowed"]
    assert r["rows"] and r["support"], "| getchecks returned no checks with an allowed role" + _diag(r)
    keys = {s.get("access_key_id") for s in r["support"]}
    assert r["issued"] and keys <= r["issued"], f"signed with {keys}, not the role's {r['issued']}" + _diag(r)


def test_getchecks_refuses_when_the_role_is_refused(runs):
    r = runs["denied"]
    assert not r["support"], (f"| getchecks called Support {len(r['support'])} times after AssumeRole "
                              "failed (the base keys' fallback)" + _diag(r))
    assert not r["rows"], f"| getchecks returned {len(r['rows'])} rows" + _diag(r)
    assert f"Could not assume role_arn={DENIED_ROLE}" in r["body"], "no error reached the search" + _diag(r)
