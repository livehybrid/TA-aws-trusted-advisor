"""
The proxy on the Configuration page's Proxy tab carries the collector's AWS
calls (and the getchecks search command's), and only while it is enabled.

docker-compose.yml runs a Squid forward proxy (docker/proxy) beside Splunk and
the mock AWS: port 3128 open, 3129 with basic auth. The proxy is configured
through the add-on's UCC settings handler, the endpoint the Proxy tab saves
to, so the password is stored encrypted and read back the way the collector
reads it.

Real AWS endpoints are https, and botocore picks a proxy by the endpoint's
scheme, so the https phases are the ones that matter: there the proxy only
relays a CONNECT tunnel (it logs CONNECT mockaws:8443, never the operation)
and the mock, which serves TLS on 8443 with a certificate the Splunk container
trusts, records what came through it. The http phase is the plain case, where
the proxy sees every POST in full.

Phases, one after the other (the proxy and the endpoint override are global):
  https_open   https endpoint, proxy on 3128; `| getchecks` runs here too
  https_auth   https endpoint, proxy on 3129 with user name and password, and a
               role, so STS AssumeRole goes through the proxy as well
  http_open    http endpoint, proxy on 3128
  disabled     https endpoint, Enable unticked with host and port still filled
Each phase runs its own input once, with its own access key and index.

What a call did is read from two places the collector never consults: the
proxy's access log (`docker logs`, PROXYLOG lines) and the address and port
the mock recorded it arriving from and on.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
import uuid

import pytest

from conftest import CONTAINER, docker_exec

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
HANDLER = f"{NS}/TA_aws_trusted_advisor_aws_trusted_advisor"
INPUTS = f"{NS}/data/inputs/aws_trusted_advisor"
SETTINGS = f"{NS}/TA_aws_trusted_advisor_settings/proxy"
CONF = f"{NS}/configs/conf-ta_aws_trusted_advisor_endpoints"
ROLE = "arn:aws:iam::111111111111:role/mock-trusted-advisor-reader"
PROXY_CONTAINER = os.environ.get("PROXY_CONTAINER", "ta_aws_trusted_advisor_proxy")
PROXY_HOST = "proxy"  # its name on the compose network
OPEN_PORT, AUTH_PORT = "3128", "3129"
# Matches docker/proxy/e2e-passwd. '@' and ':' must survive the proxy URL.
PROXY_USER, PROXY_PASSWORD = "e2euser", "e2e-p@ss:word"
MOCK_URL = os.environ.get("MOCK_AWS_URL", "http://127.0.0.1:8080").rstrip("/")
HTTP_ENDPOINT = os.environ.get("MOCK_AWS_URL_FROM_SPLUNK", "http://mockaws:8080")
TLS_HOSTPORT = "mockaws:8443"
HTTPS_ENDPOINT = f"https://{TLS_HOSTPORT}"


def _proxy(enabled, port=OPEN_PORT, user="", password=""):
    return {"proxy_enabled": "1" if enabled else "0", "proxy_type": "http", "proxy_url": PROXY_HOST,
            "proxy_port": port, "proxy_username": user, "proxy_password": password, "proxy_rdns": "0"}


# name -> (endpoint, proxy settings, access key, role)
PHASES = {
    "https_open": (HTTPS_ENDPOINT, _proxy(True), "AKIAPROXYTLSOPEN001", ""),
    "https_auth": (HTTPS_ENDPOINT, _proxy(True, AUTH_PORT, PROXY_USER, PROXY_PASSWORD),
                   "AKIAPROXYTLSAUTH001", ROLE),
    "http_open": (HTTP_ENDPOINT, _proxy(True), "AKIAPROXYHTTPOPEN01", ""),
    "disabled": (HTTPS_ENDPOINT, _proxy(False), "AKIAPROXYOFF0000001", ""),
}


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


def _mock(path):
    with urllib.request.urlopen(MOCK_URL + path, timeout=30) as r:
        return json.loads(r.read())


def _wait(cond, timeout, step=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        got = cond()
        if got:
            return got
        time.sleep(step)
    return cond()


def _through_proxy(port, url, auth=None):
    """curl from the Splunk container through the proxy; returns the HTTP code.

    -k: curl checks the system trust store, which the CI step does not touch.
    """
    cmd = ["curl", "-s", "-k", "-o", "/dev/null", "-w", "%{http_code}", "-x", f"http://{PROXY_HOST}:{port}"]
    if auth:
        cmd += ["--proxy-user", auth]
    rc, out, err = docker_exec(*cmd, url, timeout=60)
    return out.strip()


def _endpoint(splunk, url):
    for stanza in ("support", "sts"):
        st, body = splunk.request("POST", CONF, data={"name": stanza, "endpoint_url": url})
        if st == 409:
            st, body = splunk.request("POST", f"{CONF}/{stanza}", data={"endpoint_url": url})
        assert st in (200, 201), f"endpoint override [{stanza}] -> {st}: {body[:300]}"


def _calls(requests, key):
    """The mock calls signed with this key, or with credentials issued to it."""
    issued = {r["issued"]["AccessKeyId"] for r in requests
              if r.get("operation") == "AssumeRole" and r.get("access_key_id") == key and r.get("issued")}
    return [r for r in requests if r.get("access_key_id") in {key} | issued]


def _run_phase(splunk, name):
    endpoint, proxy, key, role = PHASES[name]
    index = f"aws_ta_px_{name}"
    st, body = splunk.request("POST", "/services/data/indexes", data={"name": index})
    assert st in (200, 201, 409), f"create index {index} -> {st}: {body[:300]}"
    _endpoint(splunk, endpoint)
    st, body = splunk.request("POST", SETTINGS, data=proxy, params={"output_mode": "json"})
    assert st == 200, f"save Proxy tab -> {st}: {body[:400]}"
    log_from = len(proxy_log())
    input_name = f"px_{name}"
    data = {"name": input_name, "interval": "3600", "index": index, "aws_access_key": key,
            "aws_secret_key": "mock-secret-not-real"}
    if role:
        data["role_arn"] = role
    st, body = splunk.request("POST", HANDLER, data=data)
    assert st in (200, 201), f"create {input_name} -> {st}: {body[:400]}"
    try:
        st, body = splunk.request("POST", f"{INPUTS}/{input_name}/enable")
        assert st == 200, f"enable {input_name} -> {st}: {body[:300]}"
        events = _wait(lambda: splunk.search(f"search index={index} earliest=0 | head 5",
                                             earliest="0", count=5), 180, 10)
        time.sleep(5)
        getchecks = _getchecks(splunk) if name == "https_open" else None
    finally:
        splunk.request("POST", f"{INPUTS}/{input_name}/disable")
        splunk.request("DELETE", f"{HANDLER}/{input_name}")
    return {"events": events, "log": proxy_log()[log_from:], "calls": _calls(_mock("/_mock/requests"), key),
            "endpoint": endpoint, "getchecks": getchecks}


def _getchecks(splunk):
    """Run `| getchecks` in the app (it reads the app's first input's keys)."""
    calls_from, log_from = len(_mock("/_mock/requests")), len(proxy_log())
    st, body = splunk.request("POST", f"/servicesNS/admin/{APP}/search/jobs/oneshot",
                              data={"search": "| getchecks", "output_mode": "json", "count": 0})
    time.sleep(3)
    return {"status": st, "rows": json.loads(body).get("results", []) if st == 200 else body[:400],
            "calls": [r for r in _mock("/_mock/requests")[calls_from:]
                      if r.get("operation") == "DescribeTrustedAdvisorChecks"],
            "log": proxy_log()[log_from:]}


@pytest.fixture(scope="module")
def phases(splunk):
    ips = {"proxy": _ip(PROXY_CONTAINER), "splunk": _ip(CONTAINER)}
    try:
        yield {"ips": ips, **{name: _run_phase(splunk, name) for name in PHASES}}
    finally:
        splunk.request("POST", SETTINGS, params={"output_mode": "json"},
                       data={"proxy_enabled": "0", "proxy_url": "", "proxy_port": "", "proxy_username": "",
                             "proxy_password": "", "proxy_rdns": "0"})
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{CONF}/{stanza}")


def _diag(phase, splunk=None):
    rows = [f"  {r.get('port')} {r.get('status')} {r.get('method')} {r.get('url')} user={r.get('user')}"
            for r in phase["log"][-15:]]
    calls = [(r.get("operation"), r.get("client"), "tls" if r.get("tls") else "plain", r.get("error"))
             for r in phase["calls"][:10]]
    text = (f"\nendpoint {phase['endpoint']}\nproxy log in this phase ({len(phase['log'])} lines, last 15):\n"
            + "\n".join(rows) + f"\nmock calls for this key (op, client, port, error): {calls}")
    if splunk is not None:
        hits = splunk.search("search index=_internal earliest=-20m (source=*ta_aws_trusted_advisor* OR source=*ta-aws-trusted-advisor* OR source=*getchecks*) | head 15 | table _raw",
                             earliest="-20m", count=15)
        text += "\ncollector log:\n" + "\n".join("  " + h.get("_raw", "")[:300] for h in hits)
    return text


def test_mock_serves_tls():
    # Precondition: without the https port the CONNECT phases test nothing.
    assert _through_proxy(OPEN_PORT, f"{HTTPS_ENDPOINT}/_mock/health") == "200", (
        "the mock does not answer https through the proxy (CI writes docker/mock/tls and trusts it)")


@pytest.mark.parametrize("name,port,user", [("https_open", OPEN_PORT, "-"),
                                            ("https_auth", AUTH_PORT, PROXY_USER)])
def test_https_calls_are_tunnelled_through_the_proxy(phases, splunk, name, port, user):
    p = phases[name]
    assert p["events"], f"phase {name} indexed nothing" + _diag(p, splunk)
    assert p["calls"], "the mock received no call signed with this phase's key" + _diag(p, splunk)
    tunnels = [r for r in p["log"] if r.get("method") == "CONNECT" and r.get("url") == TLS_HOSTPORT
               and r.get("port") == port and r.get("user") == user and r.get("status", "").endswith("/200")]
    assert tunnels, f"no CONNECT {TLS_HOSTPORT} in the proxy's log on :{port} as {user}" + _diag(p, splunk)
    direct = [r for r in p["calls"] if r.get("client") != phases["ips"]["proxy"] or not r.get("tls")]
    assert not direct, (f"{len(direct)} of {len(p['calls'])} calls did not come through the proxy over TLS "
                        f"(proxy is {phases['ips']['proxy']})" + _diag(p))


def test_getchecks_command_goes_through_the_proxy(phases):
    g = phases["https_open"]["getchecks"]
    assert g["status"] == 200 and g["rows"], f"| getchecks -> {g['status']}: {g['rows']}"
    assert g["calls"], "| getchecks never reached the mock"
    direct = [r for r in g["calls"] if r.get("client") != phases["ips"]["proxy"] or not r.get("tls")]
    assert not direct, f"| getchecks went direct: {[(r.get('client'), r.get('tls')) for r in direct]}"
    assert [r for r in g["log"] if r.get("method") == "CONNECT" and r.get("url") == TLS_HOSTPORT], g["log"]


def test_assume_role_goes_through_the_proxy_too(phases):
    p = phases["https_auth"]
    ops = [r.get("operation") for r in p["calls"]]
    assert "AssumeRole" in ops and any(o and o != "AssumeRole" for o in ops), (
        f"expected AssumeRole and Support calls through the proxy, got {ops}" + _diag(p))


def test_http_calls_go_through_the_proxy(phases, splunk):
    p = phases["http_open"]
    assert p["events"], "phase http_open indexed nothing" + _diag(p, splunk)
    assert p["calls"], "the mock received no call signed with this phase's key" + _diag(p, splunk)
    relayed = [r for r in p["log"] if r.get("method") == "POST" and r.get("url", "").startswith(HTTP_ENDPOINT)
               and r.get("port") == OPEN_PORT and r.get("status", "").endswith("/200")]
    assert relayed, f"no POST {HTTP_ENDPOINT} in the proxy's log" + _diag(p, splunk)
    direct = [r for r in p["calls"] if r.get("client") != phases["ips"]["proxy"]]
    assert not direct, f"{len(direct)} calls reached the mock directly" + _diag(p)


def test_auth_proxy_really_demands_credentials(phases):
    # Without this the auth phase could pass with no credentials sent at all.
    assert _through_proxy(AUTH_PORT, f"{HTTP_ENDPOINT}/_mock/health") == "407"
    assert _through_proxy(AUTH_PORT, f"{HTTP_ENDPOINT}/_mock/health",
                          auth=f"{PROXY_USER}:{PROXY_PASSWORD}") == "200"
    denied = [r for r in phases["https_auth"]["log"] if r.get("status", "").endswith("/407")]
    assert not denied, f"the collector was refused by the auth proxy: {denied[:5]}"


def test_disabled_proxy_is_not_used(phases, splunk):
    p = phases["disabled"]
    assert p["events"] and p["calls"], "the disabled phase collected nothing" + _diag(p, splunk)
    via_proxy = [r for r in p["log"] if "mockaws" in r.get("url", "")]
    assert not via_proxy, f"calls went through a disabled proxy: {via_proxy[:5]}"
    clients = {r.get("client") for r in p["calls"]}
    assert clients == {phases["ips"]["splunk"]}, (
        f"with the proxy off every call should come from Splunk {phases['ips']['splunk']}, got {clients}")


def test_proxy_log_check_can_see_a_request():
    # Positive control for the disabled phase: a request that does go through
    # the proxy shows up to the same reader, so an empty result means something.
    probe = uuid.uuid4().hex
    # The mock answers an unknown path 404, which still means it was reached.
    assert _through_proxy(OPEN_PORT, f"{HTTP_ENDPOINT}/_mock/probe-{probe}") == "404"
    assert _wait(lambda: [r for r in proxy_log() if probe in r.get("url", "")], 20, 2), \
        f"probe {probe} not in the proxy log"
