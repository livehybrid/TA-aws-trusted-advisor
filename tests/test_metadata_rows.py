"""
Unit tests for common.get_cleaned_metadata_values / merge_metadata: bucket rows.

Trusted Advisor returns some flagged resources as "bucket" rows: metadata whose
first value is empty, continuing the row before it. Their last value joins the
previous row's last value as a list. That crashed (AttributeError: 'str' object
has no attribute 'append') whenever the previous row's last value was a
string, which is the usual case.

No Splunk needed. common.py imports Splunk-only modules at the top, so they are
stubbed here; the functions under test touch none of them. Expected rows are
written out literally, not rebuilt with the code's own logic.
"""
from __future__ import annotations

import copy
import importlib
import os
import sys
import types

import pytest

BIN = os.path.join(os.path.dirname(__file__), os.pardir, "package", "bin")


@pytest.fixture(scope="module")
def common():
    stubs = {}
    for name in ("splunk", "splunk.entity", "splunk.rest", "splunklib", "splunklib.modularinput",
                 "boto3", "botocore", "botocore.exceptions"):
        if name not in sys.modules:
            stubs[name] = types.ModuleType(name)
    if "botocore.exceptions" in stubs:
        stubs["botocore.exceptions"].EndpointConnectionError = type("EndpointConnectionError", (Exception,), {})
        stubs["botocore.exceptions"].ClientError = type("ClientError", (Exception,), {})
    if "splunklib" in stubs:
        stubs["splunklib"].modularinput = stubs.get("splunklib.modularinput", types.ModuleType("m"))
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    sys.dont_write_bytecode = True  # never leave .pyc under package/bin
    sys.path.insert(0, os.path.abspath(BIN))
    try:
        yield importlib.import_module("common")
    finally:
        sys.path.remove(os.path.abspath(BIN))
        sys.modules.pop("common", None)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _rows(*metadata):
    return [{"status": "warning", "metadata": list(m)} for m in metadata]


def test_bucket_row_after_a_string_value(common):
    result = _rows(["eu-west-2", "bucket-a", "Yellow", "s3://a/x"],
                   [None, None, None, "s3://a/y"])
    assert common.get_cleaned_metadata_values(result) == [
        ["eu-west-2", "bucket-a", "Yellow", ["s3://a/x", "s3://a/y"]],
    ]


def test_two_bucket_rows_after_a_string_value(common):
    result = _rows(["eu-west-2", "bucket-a", "Yellow", "s3://a/x"],
                   ["", "", "", "s3://a/y"],
                   [None, None, None, "s3://a/z"],
                   ["eu-west-1", "bucket-b", "Red", "s3://b/x"])
    assert common.get_cleaned_metadata_values(result) == [
        ["eu-west-2", "bucket-a", "Yellow", ["s3://a/x", "s3://a/y", "s3://a/z"]],
        ["eu-west-1", "bucket-b", "Red", "s3://b/x"],
    ]


def test_bucket_row_after_a_none_value(common):
    # The shape the old code did handle: keep it working.
    result = _rows(["eu-west-2", "bucket-a", "Yellow", None],
                   [None, None, None, "s3://a/y"])
    assert common.get_cleaned_metadata_values(result) == [
        ["eu-west-2", "bucket-a", "Yellow", ["s3://a/y"]],
    ]


def test_bucket_row_first_does_not_crash(common):
    result = _rows([None, None, None, "s3://orphan"], ["eu-west-2", "bucket-a", "Yellow", "x"])
    assert common.get_cleaned_metadata_values(result) == [
        [None, None, None, "s3://orphan"],
        ["eu-west-2", "bucket-a", "Yellow", "x"],
    ]


def test_raw_flagged_resources_are_not_modified(common):
    # The raw flaggedResources are indexed in the same event as the merged
    # metadata, so the merge must not rewrite them.
    result = _rows(["eu-west-2", "bucket-a", "Yellow", None], [None, None, None, "s3://a/y"])
    before = copy.deepcopy(result)
    common.get_cleaned_metadata_values(result)
    assert result == before


def test_merge_metadata_joins_the_bucket_values(common):
    result = _rows(["eu-west-2", "bucket-a", "Yellow", "s3://a/x"],
                   [None, None, None, "s3://a/y"],
                   [None, None, None, None])
    merged, merged_html = common.merge_metadata(result, ["Region", "Bucket", "Status", "Objects"])
    assert merged == ["Region: eu-west-2,", "Bucket: bucket-a,", "Status: Yellow,",
                      "Objects: s3://a/x, s3://a/y, ,", "---"]
    assert '<p class="yellow status"><b>Status:</b> Yellow</p>' in merged_html
