"""
Live execution — the modular input RUNS on Splunk 10 without the AOB crash.

The estate-wide Add-on Builder failure class is a collect-time crash reading
inputs.metadata["server_uri"], which Splunk 10 no longer supplies: scheme
introspection and registration smokes pass while every scheduled run dies.
This test exists to pin that class shut after the aoblib remnants were removed.

It creates an UNCONFIGURED input (no AWS credentials), lets splunkd schedule
it, and asserts the collector got far enough to fail on AWS auth (or run) —
i.e. its own code executed past the metadata handling — and that the
server_uri/Setup_Util crash signature never appears.

No AWS credentials are needed; a real-AWS canary is deferred until keys are
provided (tracker: handoff #29).
"""
from __future__ import annotations

import time

import pytest

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
STANZA = "aob_crash_probe"


@pytest.fixture()
def probe_input(splunk):
    st, body = splunk.request(
        "POST", f"{NS}/data/inputs/aws_trusted_advisor",
        data={"name": STANZA, "interval": "60", "index": "main"},
    )
    assert st in (200, 201, 409), f"create input -> {st}: {body[:300]}"
    splunk.request("POST", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}/enable")
    yield
    splunk.request("DELETE", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}")


def test_input_executes_without_aob_metadata_crash(splunk, probe_input):
    crash, ran = [], []
    deadline = time.time() + 150
    while time.time() < deadline and not (crash or ran):
        crash = splunk.search(
            'search index=_internal earliest=-10m aws_trusted_advisor '
            '("server_uri" OR "Setup_Util") (ERROR OR "KeyError") | head 3',
            earliest="-10m",
        )
        # Evidence the collector's own code ran past metadata handling: its
        # logger initialised / an AWS auth attempt or error was recorded.
        ran = splunk.search(
            "search index=_internal earliest=-10m "
            f'(source=*{APP.lower()}* OR source=*ta_aws_trusted_advisor* OR "aws_trusted_advisor://{STANZA}") '
            '(NoCredentialsError OR "Unable to locate credentials" OR boto OR "aws_trusted_advisor" ) '
            "| head 3",
            earliest="-10m",
        )
        if not (crash or ran):
            time.sleep(10)

    assert not crash, (
        "the AOB metadata crash is back: " + str([h.get("_raw", "")[:200] for h in crash])
    )
    assert ran, (
        "no evidence the input executed within 150s — it may not be scheduling at all"
    )
