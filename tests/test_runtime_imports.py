"""
The collector imports cleanly on the interpreter Splunk picks for it, and gets
as far as botocore.

python.required = 3.13 means Splunk 10.2+ runs the input on Python 3.13, where
boto3 comes from Splunk's own bundled libraries (it is not vendored). The older
live-execute test accepts any log line containing "boto", so an ImportError for
boto3 would satisfy it.

The collector writes to stderr, which splunkd records in splunkd.log as
ExecProcessor 'message from "<python> .../TA-aws-trusted-advisor/bin/aws_trusted_advisor.py" ...',
one event per line of a traceback. Both searches are scoped to exactly that
source, so nothing else in _internal can satisfy or hide them.
"""
from __future__ import annotations

import time
import warnings

APP = "TA-aws-trusted-advisor"
NS = f"/servicesNS/nobody/{APP}"
STANZA = "runtime_import_probe"
SCRIPT = f"etc/apps/{APP}/bin/aws_trusted_advisor.py"
SCOPE = f'index=_internal sourcetype=splunkd component=ExecProcessor "{SCRIPT}"'


def _raws(hits):
    return [h.get("_raw", "")[:300] for h in hits]


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
            broken = splunk.search(
                f"search {SCOPE} "
                '("No module named" OR ModuleNotFoundError OR ImportError OR SyntaxError) | head 5',
                earliest="-15m",
            )
            # botocore's own message when it looks for credentials and finds none:
            # proof that boto3 imported and a client was built on this interpreter.
            reached = splunk.search(
                f'search {SCOPE} "Unable to locate credentials" | head 3',
                earliest="-15m",
            )
            if not (broken or reached):
                time.sleep(10)
        assert not broken, "collector failed to import: " + str(_raws(broken))
        if not reached:
            seen = splunk.search(f"search {SCOPE} | head 25", earliest="-15m")
            raise AssertionError(
                f"no evidence botocore ran within 180s; collector stderr: {_raws(seen)}"
            )
        warnings.warn(f"botocore evidence: {_raws(reached)[:1]}")
    finally:
        splunk.request("DELETE", f"{NS}/data/inputs/aws_trusted_advisor/{STANZA}")
