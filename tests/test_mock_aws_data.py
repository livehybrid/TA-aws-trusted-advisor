"""
End-to-end data path: the aws_trusted_advisor input collects from a mock AWS
Support (Trusted Advisor) API and its events are INDEXED.

docker/mock/mock_aws.py runs beside Splunk in the CI compose file. The add-on
reaches it through its optional endpoint override, written here as
local/ta_aws_trusted_advisor_endpoints.conf through splunkd's configs/conf-*
endpoint (modular inputs do not inherit the container environment, so an env
var would never reach the collector).

The input is created through the add-on's UCC REST handler, so the access and
secret keys are stored encrypted, and the collector has to read them back
through the credential store to sign its calls: the mock records the access
key id it received.

The expected data comes from the mock's /_mock/dataset and /_mock/requests,
neither of which the collector consults. Fields are read back through the
search-time aliases in props.conf (check_id, resources_flagged, ...), a route
the collector never uses.

The Support API calls this collector makes (DescribeTrustedAdvisorChecks,
DescribeTrustedAdvisorCheckResult) are not paginated, and the collector keeps
no checkpoint: every run re-emits every check by design. So the guards here
are one event per check per run, not pagination or de-duplication.
"""
from __future__ import annotations

import calendar
import collections
import json
import os
import time
import urllib.request

import pytest

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
STANZA = "mock_trusted_advisor"
INDEX = "aws_ta_mock"
ACCESS_KEY = "AKIAMOCKTRUSTEDADV01"
# One run only: the input runs when it is created, and not again in the test.
INTERVAL = 3600

MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
MOCK_URL_FROM_SPLUNK = os.environ.get("MOCK_AWS_URL_FROM_SPLUNK", "http://mockaws:8080")
FIELDS = ("check_id", "status", "resources_flagged", "resources_processed", "resources_suppressed",
          "estimated_monthly_savings", "flagged_resource_id", "flagged_status", "flagged_region",
          "metadata", "metadata_html")


def _mock(path, method="GET"):
    req = urllib.request.Request(MOCK_URL + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _collector_log(splunk):
    hits = splunk.search(
        "search index=_internal earliest=-30m "
        f'(source=*ta-aws-trusted-advisor* OR source=*ta_aws_trusted_advisor* '
        f'OR (sourcetype=splunkd component=ExecProcessor "{APP}")) '
        "| head 40 | table _raw",
        earliest="-30m",
        count=40,
    )
    return [h.get("_raw", "")[:300] for h in hits]


def _mv(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _indexed(splunk):
    return splunk.search(
        f"search index={INDEX} earliest=0 | eval epoch=_time "
        f"| table sourcetype source epoch {' '.join(FIELDS)}",
        earliest="0",
        count=1000,
    )


@pytest.fixture(scope="module")
def run(splunk):
    st, body = splunk.request("POST", "/services/data/indexes", data={"name": INDEX})
    assert st in (200, 201, 409), f"create index -> {st}: {body[:300]}"

    st, body = splunk.request("POST", CONF, data={"name": "support", "endpoint_url": MOCK_URL_FROM_SPLUNK})
    if st == 409:
        st, body = splunk.request("POST", f"{CONF}/support", data={"endpoint_url": MOCK_URL_FROM_SPLUNK})
    assert st in (200, 201), f"write endpoint override -> {st}: {body[:300]}"

    _mock("/_mock/reset", "POST")
    dataset = _mock("/_mock/dataset")["support"]

    st, body = splunk.request("POST", HANDLER, data={
        "name": STANZA, "interval": str(INTERVAL), "index": INDEX,
        "aws_access_key": ACCESS_KEY, "aws_secret_key": "mock-secret-not-real",
    })
    assert st in (200, 201), f"create input via UCC handler -> {st}: {body[:400]}"
    splunk.request("POST", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}/enable")

    try:
        want = len(dataset["checks"])
        rows = []
        deadline = time.time() + 240
        while time.time() < deadline:
            rows = _indexed(splunk)
            if len(rows) >= want:
                break
            time.sleep(5)
        time.sleep(15)  # anything written late, or twice, lands too
        yield {
            "dataset": dataset,
            "rows": _indexed(splunk),
            "requests": _mock("/_mock/requests"),
            "log": _collector_log(splunk),
        }
    finally:
        splunk.request("POST", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}/disable")
        splunk.request("DELETE", f"{HANDLER}/{STANZA}")
        splunk.request("DELETE", f"{CONF}/support")


def _diag(run):
    calls = [(r["operation"], r["params"].get("checkId"), r.get("error")) for r in run["requests"]]
    return f"\nmock calls: {calls[:20]}\ncollector log: {run['log']}"


def _by_check(run):
    return {r.get("check_id"): r for r in run["rows"]}


def test_collector_called_the_mock_with_the_stored_key(run):
    reqs = run["requests"]
    assert reqs, "the collector never reached the mock AWS endpoint" + _diag(run)
    keys = {r["access_key_id"] for r in reqs}
    assert keys == {ACCESS_KEY}, f"requests signed with {keys}, expected the input's key" + _diag(run)
    errors = [r for r in reqs if r.get("error")]
    assert not errors, f"mock rejected calls: {errors[:3]}" + _diag(run)


def test_one_event_per_check(run):
    want = sorted(c["id"] for c in run["dataset"]["checks"])
    counts = collections.Counter(r.get("check_id") for r in run["rows"])
    assert sorted(counts) == want and set(counts.values()) == {1}, (
        f"indexed check_ids {dict(counts)}, expected each of {want} once" + _diag(run))


def test_events_have_the_add_on_sourcetype_and_source(run):
    assert run["rows"], "nothing indexed" + _diag(run)
    bad = [(r.get("sourcetype"), r.get("source")) for r in run["rows"]
           if r.get("sourcetype") != "aws:trustedadvisor"
           or r.get("source") != f"aws_trusted_advisor://{STANZA}"]
    assert not bad, f"wrong sourcetype/source: {bad[:3]}" + _diag(run)


def test_fields_map_from_the_check_result(run):
    results = run["dataset"]["results"]
    assert run["rows"], "nothing indexed" + _diag(run)
    for check_id, row in _by_check(run).items():
        res = results[check_id]
        summary = res["resourcesSummary"]
        flagged = res["flaggedResources"]
        assert row.get("status") == res["status"], f"{check_id} status"
        assert int(row.get("resources_flagged")) == summary["resourcesFlagged"], f"{check_id} resources_flagged"
        assert int(row.get("resources_processed")) == summary["resourcesProcessed"], f"{check_id} resources_processed"
        assert int(row.get("resources_suppressed")) == summary["resourcesSuppressed"], f"{check_id} resources_suppressed"
        assert float(row.get("estimated_monthly_savings")) == (
            res["categorySpecificSummary"]["costOptimizing"]["estimatedMonthlySavings"]), f"{check_id} savings"
        assert _mv(row.get("flagged_resource_id")) == [f["resourceId"] for f in flagged], f"{check_id} resource ids"
        assert _mv(row.get("flagged_status")) == [f["status"] for f in flagged], f"{check_id} flagged status"
        assert _mv(row.get("flagged_region")) == [f["region"] for f in flagged], f"{check_id} flagged region"


def test_metadata_is_merged_with_the_check_headers(run):
    checks = {c["id"]: c for c in run["dataset"]["checks"]}
    results = run["dataset"]["results"]
    rows = _by_check(run)
    flagged_checks = [c for c in checks if results[c]["flaggedResources"]]
    assert flagged_checks and any(c in rows for c in flagged_checks), "no flagged check indexed" + _diag(run)
    for check_id, row in rows.items():
        flagged = results[check_id]["flaggedResources"]
        if not flagged:
            assert not _mv(row.get("metadata")), f"{check_id} has nothing flagged but carries metadata"
            continue
        want = []
        for f in flagged:
            want += [f"{h}: {v}," for h, v in zip(checks[check_id]["metadata"], f["metadata"])] + ["---"]
        assert _mv(row.get("metadata")) == want, f"{check_id} metadata={row.get('metadata')}"
        html = _mv(row.get("metadata_html"))
        for f in flagged:
            colour = f["metadata"][-1].lower()
            assert f'<p class="{colour} status"><b>Status:</b> {f["metadata"][-1]}</p>' in html, (
                f"{check_id} status html for {colour}: {html}")


def test_time_is_the_check_timestamp(run):
    results = run["dataset"]["results"]
    assert run["rows"], "nothing indexed" + _diag(run)
    for check_id, row in _by_check(run).items():
        want = calendar.timegm(time.strptime(results[check_id]["timestamp"], "%Y-%m-%dT%H:%M:%SZ"))
        assert abs(float(row["epoch"]) - want) < 1, (
            f"{check_id} _time={row['epoch']} timestamp={results[check_id]['timestamp']}")
