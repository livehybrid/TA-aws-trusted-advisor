"""
A proxy that is enabled but unusable, or settings that cannot be read, stop the
input and `| getchecks` with a clear error. Nothing is sent: not direct to AWS,
not to the proxy.

Only a proxy that is explicitly not enabled (Enable unticked, or the stanza as
the add-on ships it) may go direct. The old code went direct, after at most a
warning, whenever the settings could not be read and whenever Enable was on
with no host or an unknown type.

Phases, one after the other (the proxy settings are global). The [proxy]
stanza is written through the conf endpoint, as a hand edit or a deployment
tool would write it, because the Proxy tab's own form would refuse some of
these:
  no_host           Enable on, port set, no host
  unknown_type      Enable on, a working proxy, type "ftp"
  unreadable        Enable on, password placeholder "******" with no stored
                    secret: the settings cannot be decrypted, so they cannot
                    be read
  half_credentials  Enable on, the auth proxy, a user name and no password
  as_shipped        every field as default/ta_aws_trusted_advisor_settings.conf
                    ships it (never configured): goes direct and collects. This
                    is also the positive control for the "no call" readers.
Each phase runs its own input once (its own access key and index), then
`| getchecks` with that input's keys.

What happened is read from places the add-on never consults: the mock AWS's
request log (every call, keyed by the access key that signed it), the proxy's
access log (`docker logs`) and splunkd's _internal index for the add-on's log
line.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

import pytest

from conftest import CONTAINER

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
INPUTS = f"{NS}/data/inputs/aws_trusted_advisor"
SETTINGS = f"{NS}/TA_aws_trusted_advisor_settings/proxy"
SETTINGS_CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_settings/proxy"
ENDPOINTS = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
PASSWORDS = f"{NS}/storage/passwords"
PROXY_REALM = f"__REST_CREDENTIAL__#{APP}#configs/conf-ta_aws_trusted_advisor_settings"
PROXY_CONTAINER = os.environ.get("PROXY_CONTAINER", "ta_aws_trusted_advisor_proxy")
MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
HTTPS_ENDPOINT = "https://mockaws:8443"
REFUSED = "Proxy settings unusable"

AS_SHIPPED = {"proxy_enabled": "", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
              "proxy_username": "", "proxy_password": "", "proxy_rdns": ""}


def _on(**kw):
    return {**AS_SHIPPED, "proxy_enabled": "1", "proxy_url": "proxy", "proxy_port": "3128", **kw}


# name -> (stanza, access key, reason the add-on must give; None = must collect)
PHASES = {
    "no_host": (_on(proxy_url=""), "AKIAFAILCLOSEDNOHOST", "has no host"),
    "unknown_type": (_on(proxy_type="ftp"), "AKIAFAILCLOSEDFTP001", "'ftp' is not supported"),
    "unreadable": (_on(proxy_port="3129", proxy_username="e2euser", proxy_password="******"),
                   "AKIAFAILCLOSEDUNREAD", "cannot read the [proxy] stanza"),
    "half_credentials": (_on(proxy_port="3129", proxy_username="e2euser"),
                         "AKIAFAILCLOSEDHALF01", "user name but no password"),
    "as_shipped": (dict(AS_SHIPPED), "AKIAFAILCLOSEDSHIPPD", None),
}
REFUSING = [name for name, (_, _, reason) in PHASES.items() if reason]


def _ip(container):
    out = subprocess.run(
        ["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container],
        capture_output=True, text=True, timeout=30)
    ips = out.stdout.split()
    assert out.returncode == 0 and ips, f"no address for {container}: {out.stderr}"
    return ips[0]


def proxy_log():
    """Every PROXYLOG line the proxy has written, parsed to dicts, in order."""
    out = subprocess.run(["docker", "logs", PROXY_CONTAINER], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"docker logs {PROXY_CONTAINER}: {out.stderr[-300:]}"
    rows = []
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        if line.startswith("PROXYLOG "):
            rows.append(dict(kv.split("=", 1) for kv in line.split()[1:] if "=" in kv))
    return rows


def _requests():
    with urllib.request.urlopen(MOCK_URL + "/_mock/requests", timeout=30) as r:
        return json.loads(r.read())


def _calls(key):
    """Every mock call signed with this key, or with credentials issued to it."""
    reqs = _requests()
    issued = {r["issued"]["AccessKeyId"] for r in reqs
              if r.get("operation") == "AssumeRole" and r.get("access_key_id") == key and r.get("issued")}
    return [r for r in reqs if r.get("access_key_id") in {key} | issued]


def _wait(cond, timeout, step=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = cond()
        if got:
            return got
        time.sleep(step)
    return cond()


def _refusals(splunk, input_name):
    return splunk.search(f'search index=_internal earliest=-30m "input={input_name}" "{REFUSED}" '
                         f'| head 5 | table _raw', earliest="-30m", count=5)


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


def _getchecks(splunk):
    st, body = splunk.request("POST", f"/servicesNS/admin/{APP}/search/jobs/oneshot",
                              data={"search": "| getchecks", "output_mode": "json", "count": 0})
    try:
        rows = json.loads(body).get("results", [])
    except ValueError:
        rows = []
    return {"status": st, "body": search_text(body)[:3000], "rows": [r for r in rows if "ERROR" not in r]}


def _write_stanza(splunk, stanza):
    st, body = splunk.request("POST", SETTINGS_CONF, data=stanza)
    assert st == 200, f"write [proxy] -> {st}: {body[:400]}"


def _proxy_realms(splunk):
    return {e["content"].get("realm") for e in splunk.entries(PASSWORDS)}


def _run_phase(splunk, name):
    stanza, key, reason = PHASES[name]
    index = f"aws_ta_fc_{name}"
    st, body = splunk.request("POST", "/services/data/indexes", data={"name": index})
    assert st in (200, 201, 409), f"create index {index} -> {st}: {body[:300]}"
    # No stored proxy secret in any phase: in `unreadable` that is what makes
    # the placeholder undecryptable.
    splunk.request("DELETE", f"{PASSWORDS}/" + urllib.parse.quote(f"{PROXY_REALM}:proxy:", safe=""))
    _write_stanza(splunk, stanza)
    out = {"realms": _proxy_realms(splunk), "log_from": len(proxy_log())}
    input_name = f"aaa_fc_{name}"  # sorts first: getchecks uses the first input
    st, body = splunk.request("POST", HANDLER, data={
        "name": input_name, "interval": "3600", "index": index, "aws_access_key": key,
        "aws_secret_key": "mock-secret-not-real"})
    assert st in (200, 201), f"create {input_name} -> {st}: {body[:400]}"
    try:
        st, body = splunk.request("POST", f"{INPUTS}/{input_name}/enable")
        assert st == 200, f"enable {input_name} -> {st}: {body[:300]}"
        events = lambda: splunk.search(f"search index={index} earliest=0 | head 5", earliest="0", count=5)
        _wait(lambda: _refusals(splunk, input_name) or _calls(key) or events(), 180)
        time.sleep(15)  # anything the run still had to send
        out["events"] = events()
        out["refusals"] = _refusals(splunk, input_name)
        out["input_calls"] = _calls(key)
        splunk.request("POST", f"{INPUTS}/{input_name}/disable")
        calls_before = len(_calls(key))
        out["getchecks"] = _getchecks(splunk)
        time.sleep(3)
        out["getchecks"]["calls"] = _calls(key)[calls_before:]
    finally:
        splunk.request("POST", f"{INPUTS}/{input_name}/disable")
        splunk.request("DELETE", f"{HANDLER}/{input_name}")
    out["proxy_log"] = [r for r in proxy_log()[out["log_from"]:] if "mockaws" in r.get("url", "")]
    out["calls"] = _calls(key)
    out["input_name"] = input_name
    return out


@pytest.fixture(scope="module")
def phases(splunk):
    for stanza in ("support", "sts"):
        st, body = splunk.request("POST", ENDPOINTS, data={"name": stanza, "endpoint_url": HTTPS_ENDPOINT})
        if st == 409:
            st, body = splunk.request("POST", f"{ENDPOINTS}/{stanza}", data={"endpoint_url": HTTPS_ENDPOINT})
        assert st in (200, 201), f"endpoint override [{stanza}] -> {st}: {body[:300]}"
    try:
        yield {"ips": {"splunk": _ip(CONTAINER)}, **{name: _run_phase(splunk, name) for name in PHASES}}
    finally:
        _write_stanza(splunk, AS_SHIPPED)
        splunk.request("POST", SETTINGS, params={"output_mode": "json"},
                       data={"proxy_enabled": "0", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
                             "proxy_username": "", "proxy_password": "", "proxy_rdns": "0"})
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{ENDPOINTS}/{stanza}")


def _diag(p, splunk=None):
    calls = [(r.get("operation"), r.get("client"), "tls" if r.get("tls") else "plain", r.get("error"))
             for r in p["calls"][:10]]
    text = (f"\nmock calls for this key (op, client, tls, error): {calls}"
            f"\nproxy log lines for the mock: {[(r.get('status'), r.get('method'), r.get('url')) for r in p['proxy_log'][:8]]}"
            f"\nrefusal lines: {[h.get('_raw', '')[:300] for h in p['refusals']]}"
            f"\ngetchecks -> {p['getchecks']['status']}: {p['getchecks']['body'][:600]}")
    if splunk is not None:
        hits = splunk.search(f'search index=_internal earliest=-30m "{p["input_name"]}" | head 10 | table _raw',
                             earliest="-30m", count=10)
        text += "\ncollector log:\n" + "\n".join("  " + h.get("_raw", "")[:300] for h in hits)
    return text


@pytest.mark.parametrize("name", REFUSING)
def test_no_call_reaches_aws_or_the_proxy(phases, name):
    p = phases[name]
    assert not p["calls"], (f"{len(p['calls'])} calls signed with this phase's key reached AWS "
                            f"(from {sorted({r.get('client') for r in p['calls']})}; Splunk is "
                            f"{phases['ips']['splunk']})" + _diag(p))
    assert not p["proxy_log"], "the run contacted the proxy although it refused to use it" + _diag(p)
    assert not p["events"], f"{len(p['events'])} events indexed" + _diag(p)


@pytest.mark.parametrize("name", REFUSING)
def test_refusal_is_logged_with_its_reason(phases, splunk, name):
    # Also the proof that the input ran at all in this phase.
    p = phases[name]
    reason = PHASES[name][2]
    assert p["refusals"], f"no '{REFUSED}' line for input={p['input_name']}" + _diag(p, splunk)
    assert any(reason in h.get("_raw", "") for h in p["refusals"]), f"reason '{reason}' not logged" + _diag(p)


@pytest.mark.parametrize("name", REFUSING)
def test_getchecks_refuses_with_a_search_error(phases, name):
    p = phases[name]
    g = p["getchecks"]
    assert REFUSED in g["body"] and PHASES[name][2] in g["body"], (
        "| getchecks did not report the refusal to the search" + _diag(p))
    assert not g["rows"], f"| getchecks returned {len(g['rows'])} rows" + _diag(p)
    assert not g["calls"], f"| getchecks sent {len(g['calls'])} calls" + _diag(p)


def test_unreadable_phase_really_had_no_stored_secret(phases):
    # Without this the unreadable phase could be reading a real password.
    assert PROXY_REALM not in phases["unreadable"]["realms"], phases["unreadable"]["realms"]


def test_never_configured_goes_direct(phases, splunk):
    p = phases["as_shipped"]
    assert p["events"] and p["calls"], "the as-shipped phase collected nothing" + _diag(p, splunk)
    clients = {r.get("client") for r in p["calls"]}
    assert clients == {phases["ips"]["splunk"]}, f"expected every call direct from Splunk, got {clients}"
    assert not p["refusals"], "refused although the proxy was never configured" + _diag(p)
    g = p["getchecks"]
    assert g["rows"] and g["calls"], f"| getchecks -> {g['status']}: {g['body'][:400]}"
