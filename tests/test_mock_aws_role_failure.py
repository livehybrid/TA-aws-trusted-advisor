"""
A role that cannot be assumed: the input fails cleanly.

The mock refuses AssumeRole for any role named mock-denied*. The input has
stored access keys and that role, as a user with a mistyped or revoked role
would have. It must:

  * write nothing but XML to stdout. stdout is the XML event stream splunkd
    parses (streaming_mode_xml); the old code printed "Failed to get session"
    into it;
  * never fall back to the base keys. The old code carried on with them and
    collected as a different principal;
  * log the failure where an admin will look.

stdout is checked by running the collector by hand inside the container, with
an input definition on stdin, and capturing exactly what it writes: a route the
collector does not use.

There is deliberately no check on splunkd's own logs. With the old print
restored, splunkd 10.0 and 10.6.0 logged no WARN or ERROR about the input,
the stray text reached no index, and the events after it were indexed: splunkd
drops leading non-XML text silently. A splunkd-log assertion stayed green on
that mutant, so it guarded nothing and was removed (26 Nov 2026).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import pytest

from conftest import CONTAINER, PW, USER

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
STANZA = "denied_role"
INDEX = "aws_ta_denied"
ACCESS_KEY = "AKIAMOCKTADENIED0001"
ROLE = "arn:aws:iam::111111111111:role/mock-denied-trusted-advisor"
SCRIPT = f"/opt/splunk/etc/apps/{APP}/bin/aws_trusted_advisor.py"

MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
MOCK_URL_FROM_SPLUNK = os.environ.get("MOCK_AWS_URL_FROM_SPLUNK", "http://mockaws:8080")


def _mock(path, method="GET"):
    req = urllib.request.Request(MOCK_URL + path, method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _assumes(requests):
    return [r for r in requests if r["operation"] == "AssumeRole" and r["params"].get("RoleArn") == ROLE]


def _input_xml(session_key):
    params = {"role_arn": ROLE, "index": INDEX, "host": "splunk", "sourcetype": "aws:trustedadvisor",
              "interval": "3600"}
    body = "".join(f'<param name="{k}">{escape(v)}</param>' for k, v in params.items())
    return (
        "<input><server_host>splunk</server_host><server_uri>https://127.0.0.1:8089</server_uri>"
        f"<session_key>{escape(session_key)}</session_key>"
        "<checkpoint_dir>/opt/splunk/var/lib/splunk/modinputs/aws_trusted_advisor</checkpoint_dir>"
        f'<configuration><stanza name="aws_trusted_advisor://{STANZA}">{body}</stanza></configuration>'
        "</input>"
    )


@pytest.fixture(scope="module")
def run(splunk):
    st, body = splunk.request("POST", "/services/data/indexes", data={"name": INDEX})
    assert st in (200, 201, 409), f"create index -> {st}: {body[:300]}"
    for stanza in ("support", "sts"):
        st, body = splunk.request("POST", CONF, data={"name": stanza, "endpoint_url": MOCK_URL_FROM_SPLUNK})
        if st == 409:
            st, body = splunk.request("POST", f"{CONF}/{stanza}", data={"endpoint_url": MOCK_URL_FROM_SPLUNK})
        assert st in (200, 201), f"write endpoint override [{stanza}] -> {st}: {body[:300]}"

    _mock("/_mock/reset", "POST")
    st, body = splunk.request("POST", HANDLER, data={
        "name": STANZA, "interval": "3600", "index": INDEX, "role_arn": ROLE,
        "aws_access_key": ACCESS_KEY, "aws_secret_key": "mock-secret-not-real",
    })
    assert st in (200, 201), f"create input via UCC handler -> {st}: {body[:400]}"
    started = int(time.time()) - 5
    splunk.request("POST", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}/enable")

    try:
        # 1. The scheduled run, as splunkd starts it.
        deadline = time.time() + 180
        while time.time() < deadline and not _assumes(_mock("/_mock/requests")):
            time.sleep(5)
        scheduled_assumes = len(_assumes(_mock("/_mock/requests")))

        # 2. The same input by hand, capturing exactly what reaches stdout.
        st, body = splunk.request("POST", "/services/auth/login",
                                  data={"username": USER, "password": PW, "output_mode": "json"})
        assert st == 200, f"login -> {st}"
        session_key = json.loads(body)["sessionKey"]
        p = subprocess.run(
            ["docker", "exec", "-i", "-u", "splunk", CONTAINER, "/opt/splunk/bin/splunk", "cmd", "python3", SCRIPT],
            input=_input_xml(session_key), capture_output=True, text=True, timeout=180,
        )
        time.sleep(30)  # let splunkd's own logs and any events be indexed
        yield {
            "started": started,
            "scheduled_assumes": scheduled_assumes,
            "direct": p,
            "requests": _mock("/_mock/requests"),
            "events": splunk.search(f"search index={INDEX} earliest=0 | head 20 | table _raw",
                                    earliest="0", count=20),
        }
    finally:
        splunk.request("POST", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}/disable")
        splunk.request("DELETE", f"{HANDLER}/{STANZA}")
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{CONF}/{stanza}")


def _diag(run):
    calls = [(r["operation"], r["access_key_id"], r.get("error")) for r in run["requests"]]
    d = run["direct"]
    return f"\nmock calls: {calls[:20]}\ndirect rc={d.returncode} stderr={d.stderr[-1500:]!r}"


def test_both_runs_reached_sts_with_the_stored_key(run):
    # Positive control: without this, every test below passes on an input
    # that never ran at all.
    assumes = _assumes(run["requests"])
    assert run["scheduled_assumes"] >= 1, "the scheduled run never called AssumeRole" + _diag(run)
    assert len(assumes) > run["scheduled_assumes"], "the direct run never called AssumeRole" + _diag(run)
    assert all(a["access_key_id"] == ACCESS_KEY and a.get("error") == "AccessDenied" for a in assumes), assumes


def test_direct_run_writes_only_xml_to_stdout(run):
    out = run["direct"].stdout
    if out.strip():
        try:
            ET.fromstring(out)
        except ET.ParseError as exc:
            pytest.fail(f"non-XML on stdout ({exc}): {out[:300]!r}" + _diag(run))


def test_no_fallback_to_the_base_keys(run):
    support = [r for r in run["requests"] if r["service"] == "AWSSupport_20130415"]
    assert not support, f"Support was called after AssumeRole failed: {support[:3]}" + _diag(run)
    assert not run["events"], f"{len(run['events'])} events indexed after AssumeRole failed"


def test_failure_is_logged(run, splunk):
    hits = splunk.search(
        f'search index=_internal earliest=-30m "input={STANZA}" "Could not assume role_arn={ROLE}" | head 3',
        earliest="-30m",
    )
    assert hits, "AssumeRole failure was not logged" + _diag(run)

