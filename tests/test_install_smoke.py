"""
Install / load smoke tests (network-free).

Prove the built app installs into a real Splunk cleanly: it is enabled, its
modular inputs are registered, their schemes introspect, and nothing in the app
failed to import at startup. Runs even for Dependabot PRs (no egress needed).

INPUTS is filled by the scaffolder from the app's globalConfig / bin scripts.
If it is empty (a non-input app), the input assertions skip themselves.
"""
from __future__ import annotations

import json

import pytest

APP = "TA-aws-trusted-advisor"
INPUTS = ('aws_trusted_advisor',)  # e.g. ("carbon_intensity", "nhs_ae")


def test_app_installed_and_enabled(splunk):
    entries = splunk.entries(f"/services/apps/local/{APP}")
    assert entries, f"{APP} is not installed"
    content = entries[0]["content"]
    assert content.get("disabled") in (False, 0, "0"), f"{APP} is disabled: {content.get('disabled')}"


@pytest.mark.skipif(not INPUTS, reason="app declares no modular inputs")
def test_modular_inputs_registered(splunk):
    names = {e["name"] for e in splunk.entries("/services/data/modular-inputs")}
    missing = [i for i in INPUTS if i not in names]
    assert not missing, f"modular inputs not registered: {missing} (have: {sorted(names)})"


@pytest.mark.skipif(not INPUTS, reason="app declares no modular inputs")
@pytest.mark.parametrize("inp", INPUTS)
def test_modinput_scheme_introspects(splunk, inp):
    # If a script failed to import, Splunk cannot introspect its scheme, so the
    # endpoint would error. Shape-tolerant: just assert the scheme is served.
    data = splunk.get_json(f"/services/data/modular-inputs/{inp}")
    assert data.get("entry"), f"{inp} scheme did not introspect: {json.dumps(data)[:300]}"


def test_no_startup_import_or_init_errors(splunk):
    # Precise signatures: a failed modular-input init or an import error tied to
    # this app's scripts. Deliberately does NOT match runtime fetch errors (a
    # separate concern covered by the live test). Tailor the OR-list of input
    # names if the auto-fill missed any.
    input_terms = " OR ".join(INPUTS) if INPUTS else APP.replace("-", "_")
    init_terms = " OR ".join(
        f'"Unable to initialize modular input \\"{i}\\""' for i in INPUTS
    ) or '"Unable to initialize modular input"'
    spl = (
        "search index=_internal log_level=ERROR "
        f"({init_terms} "
        'OR (("ImportError" OR "ModuleNotFoundError" OR "Traceback") '
        f"    AND ({input_terms} OR import_declare_test))) "
        "earliest=-1h"
    )
    hits = splunk.search(spl, earliest="-1h")
    assert not hits, f"startup import/init errors: {[h.get('_raw', '')[:200] for h in hits[:3]]}"
