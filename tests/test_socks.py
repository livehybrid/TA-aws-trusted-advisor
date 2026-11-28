"""
A SOCKS proxy on the Proxy tab carries the collector's AWS calls (STS and
Support) and the getchecks search command's.

botocore cannot use a SOCKS proxy by itself; package/bin/botocore_socks.py
(shared with TA-aws-health) teaches each client to. docker-compose.yml runs Dante (docker/socks) beside Splunk and the
mock AWS: 1080 takes SOCKS4 and SOCKS5 without authentication, 1081 SOCKS5
with a user name and password ('@' and ':' in it, as for the HTTP proxy).

Phases, one after the other (the proxy and the endpoint override are global):
  socks5_open   https endpoint, SOCKS5 on 1080, local DNS; `| getchecks` too
  socks5_auth   https endpoint, SOCKS5 on 1081 with credentials and remote DNS
                (socks5h: Dante resolves mockaws), and a role, so STS
                AssumeRole goes through it as well
  socks4_open   https endpoint, SOCKS4 on 1080
  socks5_http   plain-http endpoint, SOCKS5 on 1080

What a call did is read from two places the collector never consults: Dante's
own log (`docker logs`: one line per relayed connection, with the client, the
port it came in on, the authenticated user and the destination) and the
address the mock recorded each call arriving from.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

import pytest

from conftest import CONTAINER, docker_exec
from test_proxy import (APP, HANDLER, INPUTS, SETTINGS, CONF, ROLE, PROXY_USER, PROXY_PASSWORD,
                        HTTP_ENDPOINT, HTTPS_ENDPOINT, _ip, _mock, _wait, _endpoint, _calls)

SOCKS_CONTAINER = os.environ.get("SOCKS_CONTAINER", "ta_aws_trusted_advisor_socks")
SOCKS_HOST = "socks"  # its name on the compose network
OPEN_PORT, AUTH_PORT = "1080", "1081"
MOCK_CONTAINER = os.environ.get("MOCK_CONTAINER", "ta_aws_trusted_advisor_mockaws")


def _socks(kind, port=OPEN_PORT, user="", password="", rdns=False):
    return {"proxy_enabled": "1", "proxy_type": kind, "proxy_url": SOCKS_HOST, "proxy_port": port,
            "proxy_username": user, "proxy_password": password, "proxy_rdns": "1" if rdns else "0"}


# name -> (endpoint, proxy settings, access key, role)
PHASES = {
    "socks5_open": (HTTPS_ENDPOINT, _socks("socks5"), "AKIASOCKS5OPEN00001", ""),
    "socks5_auth": (HTTPS_ENDPOINT, _socks("socks5", AUTH_PORT, PROXY_USER, PROXY_PASSWORD, rdns=True),
                    "AKIASOCKS5AUTH00001", ROLE),
    "socks4_open": (HTTPS_ENDPOINT, _socks("socks4"), "AKIASOCKS4OPEN00001", ""),
    "socks5_http": (HTTP_ENDPOINT, _socks("socks5"), "AKIASOCKS5HTTP00001", ""),
}

# Dante: "... tcp/connect [: [username%e2euser@]10.0.0.3.40000 10.0.0.5.1080 -> 10.0.0.5.40001 10.0.0.2.8443"
_CONNECT = re.compile(
    r"tcp/connect\b.*?:\s+(?:(?P<method>[\w.]+)%(?P<user>[^@\s]+)@)?(?P<client>\d+\.\d+\.\d+\.\d+)\.\d+\s+"
    r"(?P<server>\S+)\.(?P<port>\d+)\s+->\s+\S+\s+(?P<dest>\S+?)\.(?P<dport>\d+)\b")


def socks_log():
    """Every connection Dante logged, parsed to dicts (raw line kept), in order."""
    out = subprocess.run(["docker", "logs", SOCKS_CONTAINER], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"docker logs {SOCKS_CONTAINER}: {out.stderr[-300:]}"
    rows = []
    for line in (out.stdout + "\n" + out.stderr).splitlines():
        m = _CONNECT.search(line)
        if m:
            rows.append({**m.groupdict(), "raw": line.strip()})
        elif "tcp/connect" in line or "error" in line.lower():
            rows.append({"raw": line.strip()})
    return rows


def _through_socks(port, url, auth=None):
    """curl from the Splunk container through the SOCKS server; returns the HTTP code."""
    cmd = ["curl", "-s", "-k", "-o", "/dev/null", "-w", "%{http_code}", "--socks5", f"{SOCKS_HOST}:{port}"]
    if auth:
        cmd += ["--proxy-user", auth]
    rc, out, err = docker_exec(*cmd, url, timeout=60)
    return out.strip()


def _getchecks(splunk):
    calls_from, log_from = len(_mock("/_mock/requests")), len(socks_log())
    st, body = splunk.request("POST", f"/servicesNS/admin/{APP}/search/jobs/oneshot",
                              data={"search": "| getchecks", "output_mode": "json", "count": 0})
    time.sleep(3)
    return {"status": st, "rows": json.loads(body).get("results", []) if st == 200 else body[:400],
            "calls": [r for r in _mock("/_mock/requests")[calls_from:]
                      if r.get("operation") == "DescribeTrustedAdvisorChecks"],
            "log": socks_log()[log_from:]}


def _run_phase(splunk, name):
    endpoint, proxy, key, role = PHASES[name]
    index = f"aws_ta_sx_{name}"
    st, body = splunk.request("POST", "/services/data/indexes", data={"name": index})
    assert st in (200, 201, 409), f"create index {index} -> {st}: {body[:300]}"
    _endpoint(splunk, endpoint)
    st, body = splunk.request("POST", SETTINGS, data=proxy, params={"output_mode": "json"})
    assert st == 200, f"save Proxy tab -> {st}: {body[:400]}"
    log_from = len(socks_log())
    input_name = f"sx_{name}"
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
        getchecks = _getchecks(splunk) if name == "socks5_open" else None
    finally:
        splunk.request("POST", f"{INPUTS}/{input_name}/disable")
        splunk.request("DELETE", f"{HANDLER}/{input_name}")
    return {"events": events, "log": socks_log()[log_from:], "calls": _calls(_mock("/_mock/requests"), key),
            "endpoint": endpoint, "getchecks": getchecks}


@pytest.fixture(scope="module")
def phases(splunk):
    ips = {"socks": _ip(SOCKS_CONTAINER), "splunk": _ip(CONTAINER), "mock": _ip(MOCK_CONTAINER)}
    try:
        yield {"ips": ips, **{name: _run_phase(splunk, name) for name in PHASES}}
    finally:
        splunk.request("POST", SETTINGS, params={"output_mode": "json"},
                       data={"proxy_enabled": "0", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
                             "proxy_username": "", "proxy_password": "", "proxy_rdns": "0"})
        for stanza in ("support", "sts"):
            splunk.request("DELETE", f"{CONF}/{stanza}")


def _diag(phase, splunk=None):
    rows = ["  " + r["raw"][-220:] for r in phase["log"][-15:]]
    calls = [(r.get("operation"), r.get("client"), "tls" if r.get("tls") else "plain", r.get("path"), r.get("error"))
             for r in phase["calls"][:10]]
    text = (f"\nendpoint {phase['endpoint']}\nSOCKS log in this phase ({len(phase['log'])} lines, last 15):\n"
            + "\n".join(rows) + f"\nmock calls for this key (op, client, tls, path, error): {calls}")
    if splunk is not None:
        hits = splunk.search("search index=_internal earliest=-20m (source=*ta_aws_trusted_advisor* OR source=*ta-aws-trusted-advisor* OR source=*getchecks*) | head 15 | table _raw",
                             earliest="-20m", count=15)
        text += "\ncollector log:\n" + "\n".join("  " + h.get("_raw", "")[:300] for h in hits)
    return text


def _relayed(phases, phase, port, dest, dport, user=None):
    """Dante's lines for this phase: Splunk -> SOCKS :port -> dest:dport (as user)."""
    return [r for r in phase["log"] if r.get("client") == phases["ips"]["splunk"] and r.get("port") == port
            and r.get("dest") == dest and r.get("dport") == dport and r.get("user") == user]


def _direct(phases, calls, tls=True):
    return [r for r in calls if r.get("client") != phases["ips"]["socks"] or bool(r.get("tls")) != tls]


def test_socks_helper_adapts_a_client_on_splunks_own_botocore():
    # botocore_socks relies on botocore internals and self-checks them; on a
    # botocore that moved them enable_socks() raises. Run as the add-on runs,
    # with its lib/ first (its urllib3 and PySocks).
    code = ("import sys; b='/opt/splunk/etc/apps/{0}/'; sys.path[:0] = [b + 'bin', b + 'lib']\n"
            "import boto3, botocore, urllib3; from botocore.config import Config; import botocore_socks as s\n"
            "def c(p): return boto3.client('support', region_name='us-east-1', aws_access_key_id='a',"
            " aws_secret_access_key='b', config=Config(proxies={{'http': p, 'https': p}}))\n"
            "x = c('socks5h://u:p%40ss@socks:1081'); assert s.enable_socks(x) and s.enable_socks(x)\n"
            "assert not s.enable_socks(c('http://proxy:3128'))\n"
            "print('ok', botocore.__version__, urllib3.__version__)").format(APP)
    pythons = ["python3"] + [p for p in ("python3.13",)
                             if docker_exec("test", "-x", f"/opt/splunk/bin/{p}")[0] == 0]
    for py in pythons:
        rc, out, err = docker_exec("/opt/splunk/bin/splunk", "cmd", py, "-c", code)
        assert rc == 0 and out.startswith("ok "), f"{py}: rc={rc} out={out!r} err={err[-800:]}"


@pytest.mark.parametrize("name", ["socks5_open", "socks4_open"])
def test_https_calls_go_through_the_open_socks_server(phases, splunk, name):
    p = phases[name]
    assert p["events"], f"phase {name} indexed nothing" + _diag(p, splunk)
    assert p["calls"], "the mock received no call signed with this phase's key" + _diag(p, splunk)
    # Local DNS: this host resolved mockaws, so Dante was given the address.
    assert _relayed(phases, p, OPEN_PORT, phases["ips"]["mock"], "8443"), (
        f"Dante logged no Splunk -> :{OPEN_PORT} -> {phases['ips']['mock']}.8443" + _diag(p, splunk))
    direct = _direct(phases, p["calls"])
    assert not direct, (f"{len(direct)} of {len(p['calls'])} calls did not come through the SOCKS server "
                        f"over TLS (it is {phases['ips']['socks']})" + _diag(p))


def test_authenticated_socks5_with_remote_dns(phases, splunk):
    p = phases["socks5_auth"]
    assert p["events"], "phase socks5_auth indexed nothing" + _diag(p, splunk)
    # Remote DNS: Dante was given the name, and logs the user it authenticated.
    assert _relayed(phases, p, AUTH_PORT, "mockaws", "8443", user=PROXY_USER), (
        f"Dante logged no {PROXY_USER} -> :{AUTH_PORT} -> mockaws.8443" + _diag(p, splunk))
    direct = _direct(phases, p["calls"])
    assert p["calls"] and not direct, f"{len(direct)} calls did not come through the SOCKS server" + _diag(p)


def test_assume_role_goes_through_the_socks_server_too(phases):
    p = phases["socks5_auth"]
    assumes = [r for r in p["calls"] if r.get("operation") == "AssumeRole"]
    signed = [r for r in p["calls"] if r.get("operation") not in (None, "AssumeRole")]
    assert assumes and signed, f"expected AssumeRole and calls with its credentials, got {p['calls']}" + _diag(p)
    assert not _direct(phases, assumes), f"STS AssumeRole did not come through the SOCKS server" + _diag(p)


def test_http_calls_go_through_the_socks_server(phases, splunk):
    p = phases["socks5_http"]
    assert p["events"] and p["calls"], "phase socks5_http collected nothing" + _diag(p, splunk)
    assert _relayed(phases, p, OPEN_PORT, phases["ips"]["mock"], "8080"), (
        f"Dante logged no Splunk -> :{OPEN_PORT} -> {phases['ips']['mock']}.8080" + _diag(p, splunk))
    assert not _direct(phases, p["calls"], tls=False), "calls reached the mock directly" + _diag(p)
    # SOCKS is a tunnel: the request line is origin-form, as with no proxy.
    paths = {r.get("path") for r in p["calls"]}
    assert paths == {"/"}, f"request targets {paths}" + _diag(p)


def test_getchecks_command_goes_through_the_socks_server(phases):
    g = phases["socks5_open"]["getchecks"]
    assert g["status"] == 200 and g["rows"], f"| getchecks -> {g['status']}: {g['rows']}"
    assert g["calls"], "| getchecks never reached the mock"
    direct = _direct(phases, g["calls"])
    assert not direct, f"| getchecks went direct: {[(r.get('client'), r.get('tls')) for r in direct]}"
    assert [r for r in g["log"] if r.get("client") == phases["ips"]["splunk"] and r.get("dport") == "8443"], \
        [r["raw"] for r in g["log"]]


def test_auth_socks_server_really_demands_credentials(phases):
    # Without this the auth phase could pass with no credentials sent at all.
    url = f"{HTTP_ENDPOINT}/_mock/health"
    assert _through_socks(AUTH_PORT, url) != "200"
    assert _through_socks(AUTH_PORT, url, auth=f"{PROXY_USER}:{PROXY_PASSWORD}") == "200"
    # Positive control for the log reader: that probe is in Dante's log, as the user.
    assert _wait(lambda: [r for r in socks_log() if r.get("user") == PROXY_USER and r.get("port") == AUTH_PORT
                          and r.get("dport") == "8080"], 20, 2), [r["raw"] for r in socks_log()[-10:]]
