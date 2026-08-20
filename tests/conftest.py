"""
Shared fixtures for the TA-aws-trusted-advisor integration suite.

Backend-agnostic: everything talks to the Splunk management API over stdlib
urllib, so the only dependency is pytest and the suite runs unchanged against
CI docker, a live Splunk, or a Portainer-launched one — only SPLUNK_MGMT_URL
changes.

Environment (defaults suit the docker harness):
  SPLUNK_MGMT_URL   management API base   (default https://127.0.0.1:8089)
  SPLUNK_USER       admin user            (default admin)
  SPLUNK_PASSWORD   admin password        (default Changeme1!)
  SPLUNK_CONTAINER  container name for     (default ta_aws_trusted_advisor_splunk)
                    `docker exec` smokes  (unused off the docker backend)
"""
from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

MGMT = os.environ.get("SPLUNK_MGMT_URL", "https://127.0.0.1:8089").rstrip("/")
USER = os.environ.get("SPLUNK_USER", "admin")
PW = os.environ.get("SPLUNK_PASSWORD", "Changeme1!")
CONTAINER = os.environ.get("SPLUNK_CONTAINER", "ta_aws_trusted_advisor_splunk")
APP = "TA-aws-trusted-advisor"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_AUTH = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()


class Splunk:
    """Minimal management-API client (urllib, JSON output_mode)."""

    def request(self, method, path, data=None, params=None):
        url = MGMT + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", _AUTH)
        try:
            with urllib.request.urlopen(req, context=_CTX, timeout=90) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get_json(self, path, **params):
        params.setdefault("output_mode", "json")
        status, body = self.request("GET", path, params=params)
        assert status == 200, f"GET {path} -> {status}: {body[:400]}"
        return json.loads(body)

    def entries(self, path, **params):
        """Return the .entry list of a collection endpoint (unpaginated).

        Splunk collection endpoints default to count=30; on a shared dev
        instance with many apps the entries under test can fall off the first
        page and vanish silently. count=0 returns everything.
        """
        params.setdefault("count", 0)
        return self.get_json(path, **params).get("entry", [])

    def search(self, spl, earliest="-7d", latest="now", count=100):
        """Blocking oneshot search -> list of result dicts."""
        if not spl.lstrip().startswith("|") and not spl.lstrip().lower().startswith("search"):
            spl = "search " + spl
        status, body = self.request(
            "POST",
            "/services/search/jobs/oneshot",
            data={
                "search": spl,
                "output_mode": "json",
                "earliest_time": earliest,
                "latest_time": latest,
                "count": count,
            },
        )
        assert status == 200, f"oneshot search -> {status}: {body[:400]}"
        return json.loads(body).get("results", [])


def docker_exec(*cmd, timeout=180):
    """Run a command inside the Splunk container. Returns (rc, stdout, stderr).

    Only meaningful on the docker/portainer backends where CONTAINER is a real
    container reachable from this host. Skips cleanly elsewhere.

    Runs as the `splunk` user: scripts opened via `splunk cmd python ...
    --scheme` import solnlib, which opens $SPLUNK_HOME/var/log/python.log at
    import — a path only the splunk user owns. A bare `docker exec` (image
    build-time user) dies with PermissionError before emitting any scheme.
    """
    full = ["docker", "exec", "-u", "splunk", CONTAINER, *cmd]
    p = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture(scope="session")
def splunk():
    """A ready Splunk client; blocks until the management API answers."""
    c = Splunk()
    deadline = time.time() + 300
    last = "no attempt"
    while time.time() < deadline:
        try:
            status, body = c.request("GET", "/services/server/info", params={"output_mode": "json"})
            if status == 200:
                return c
            last = f"{status}: {body[:200]}"
        except Exception as exc:  # connection refused while Splunk boots
            last = repr(exc)
        time.sleep(5)
    pytest.fail(f"Splunk management API not ready at {MGMT} within 300s (last: {last})")
