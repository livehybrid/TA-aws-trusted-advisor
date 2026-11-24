"""
The collector imports cleanly on the interpreter Splunk picks for it.

python.required = 3.13 means Splunk 10.2+ runs the input on Python 3.13, where
boto3 comes from Splunk's own bundled libraries (it is not vendored). The older
live-execute test accepts any log line containing "boto", so an ImportError for
boto3 would satisfy it. This one reads splunkd's ExecProcessor stderr for the
input and fails on any import or syntax error, then requires positive evidence
that botocore itself ran: the credential lookup that an unconfigured input hits.
"""
from __future__ import annotations

import time

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
STANZA = "runtime_import_probe"


def test_input_imports_and_reaches_botocore(splunk):
    st, body = splunk.request(
        "POST", f"{NS}/data/inputs/aws_trusted_advisor",
        data={"name": STANZA, "interval": "60", "index": "main"},
    )
    assert st in (200, 201, 409), f"create input -> {st}: {body[:300]}"
    try:
        broken, reached = [], []
        deadline = time.time() + 180
        while time.time() < deadline and not (broken or reached):
            # The collector's own exceptions land in the add-on's log file; a
            # crash before logging is set up lands in ExecProcessor's stderr.
            broken = splunk.search(
                "search index=_internal "
                f'(source=*ta_aws_trusted_advisor* OR (component=ExecProcessor "{APP}")) '
                "(ModuleNotFoundError OR ImportError OR SyntaxError) | head 5",
                earliest="-15m",
            )
            # Positive evidence must come from this probe's own run, in the
            # add-on's log file, so nothing else in _internal can satisfy it.
            reached = splunk.search(
                "search index=_internal source=*ta_aws_trusted_advisor* "
                f'"{STANZA}" '
                '(NoCredentialsError OR "Unable to locate credentials" OR '
                '"security token included in the request is invalid" OR InvalidClientTokenId) '
                "| head 3",
                earliest="-15m",
            )
            if not (broken or reached):
                time.sleep(10)
        assert not broken, "collector failed to import: " + str(
            [h.get("_raw", "")[:300] for h in broken]
        )
        if not reached:
            seen = splunk.search(
                "search index=_internal (\"" + STANZA + "\" OR aws_trusted_advisor) "
                "NOT sourcetype=splunkd_access NOT sourcetype=splunkd_ui_access "
                "| head 25 | table source _raw",
                earliest="-15m",
            )
            raise AssertionError(
                f"no evidence botocore ran for {STANZA} within 180s; add-on log: "
                + str([(h.get("source", ""), h.get("_raw", "")[:250]) for h in seen])
            )
    finally:
        splunk.request("DELETE", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}")
