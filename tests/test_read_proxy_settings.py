"""common.read_proxy_settings / support_client: unreadable proxy settings stop
the run before any AWS client exists.

No Splunk. solnlib and boto3 are stubbed: ConfManager is made to fail the way
solnlib fails (no such stanza, no such conf file, a password placeholder with
no stored secret), and boto3.client records every client asked for. A refusal
must raise ProxyUnusable and ask for no client at all. Each case has a positive
control (the same stub with readable settings does build a client), so "no
client" cannot pass because the stub never records anything.

The branches matter because two of them used to go direct silently: solnlib's
ConfManagerException and ConfStanzaNotExistException were taken to mean "never
configured", and any other exception logged a warning and called AWS without
the proxy. The add-on ships [proxy] in default/, so neither is "never
configured"; tests/test_proxy_fail_closed.py proves the decrypt failure end to
end.
"""
from __future__ import annotations

import importlib
import os
import sys
import types

import pytest

BIN = os.path.join(os.path.dirname(__file__), os.pardir, "package", "bin")
AS_SHIPPED = {"proxy_enabled": "", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
              "proxy_username": "", "proxy_password": "", "proxy_rdns": ""}


class ConfManagerException(Exception):
    pass


class ConfStanzaNotExistException(Exception):
    pass


class CredentialNotExistException(Exception):
    pass


class _World:
    """What the stubbed solnlib returns or raises, and what boto3 was asked for."""
    failure = None
    stanza = None
    clients = []


class _ConfFile:
    def get(self, name):
        assert name == "proxy"
        if _World.failure == "stanza":
            raise ConfStanzaNotExistException("Stanza: proxy does not exist in ta_aws_trusted_advisor_settings.conf")
        if _World.failure == "decrypt":
            raise CredentialNotExistException("Failed to get password of realm=..., user=proxy.")
        return dict(_World.stanza)


class _ConfManager:
    def __init__(self, session_key, app, realm=None, **kw):
        self.realm = realm

    def get_conf(self, name):
        if _World.failure == "conf":
            raise ConfManagerException("Config file: {} does not exist.".format(name))
        if _World.failure == "http":
            raise OSError("splunkd returned 503")
        return _ConfFile()


class _FakeClient:
    class meta:
        class config:
            proxies = None


def _client(service, **kw):
    _World.clients.append(service)
    return _FakeClient()


@pytest.fixture(scope="module")
def common():
    solnlib = types.ModuleType("solnlib")
    cm = types.ModuleType("solnlib.conf_manager")
    cm.ConfManager = _ConfManager
    cm.ConfManagerException = ConfManagerException
    cm.ConfStanzaNotExistException = ConfStanzaNotExistException
    solnlib.conf_manager = cm
    stubs = {"solnlib": solnlib, "solnlib.conf_manager": cm}
    for name in ("splunk", "splunk.entity", "splunk.rest", "splunklib", "splunklib.modularinput",
                 "boto3", "botocore", "botocore.exceptions"):
        stubs[name] = types.ModuleType(name)
    stubs["botocore.exceptions"].EndpointConnectionError = type("EndpointConnectionError", (Exception,), {})
    stubs["botocore.exceptions"].ClientError = type("ClientError", (Exception,), {})
    stubs["splunklib"].modularinput = stubs["splunklib.modularinput"]
    stubs["boto3"].client = _client
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    sys.dont_write_bytecode = True  # never leave .pyc under package/bin
    sys.path.insert(0, os.path.abspath(BIN))
    try:
        yield importlib.import_module("common")
    finally:
        sys.path.remove(os.path.abspath(BIN))
        for mod in ("common", "aws_proxy", "aws_endpoint", "botocore_socks"):
            sys.modules.pop(mod, None)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(autouse=True)
def world():
    _World.failure, _World.stanza, _World.clients = None, dict(AS_SHIPPED), []
    yield _World


@pytest.mark.parametrize("failure", ["stanza", "conf", "decrypt", "http"])
def test_unreadable_settings_refuse_before_any_client(common, world, failure):
    world.failure = failure
    with pytest.raises(common.ProxyUnusable) as e:
        common.support_client("key", "AKIA", "secret", role_arn="arn:aws:iam::1:role/r")
    assert "cannot read the [proxy] stanza" in str(e.value)
    assert world.clients == [], f"an AWS client was built after a {failure} failure: {world.clients}"


@pytest.mark.parametrize("failure", ["stanza", "conf", "decrypt", "http"])
def test_read_failures_are_errors_not_no_proxy(common, world, failure):
    world.failure = failure
    with pytest.raises(common.ProxySettingsError):
        common.read_proxy_settings("key")


def test_enabled_but_unusable_refuses_before_any_client(common, world):
    world.stanza = {**AS_SHIPPED, "proxy_enabled": "1", "proxy_port": "3128"}  # no host
    with pytest.raises(common.ProxyUnusable) as e:
        common.support_client("key", "AKIA", "secret")
    assert "no host" in str(e.value)
    assert world.clients == []


def test_never_configured_builds_the_client_direct(common, world):
    # Positive control for the refusals above: the same stubs, readable settings.
    assert common.read_proxy_settings("key") == AS_SHIPPED
    common.support_client("key", "AKIA", "secret", role_arn="")
    assert world.clients == ["support"]


def test_refusal_is_a_collector_setup_failure(common):
    # The input stops on CollectorSetupFailed (logged, nothing written); the
    # getchecks command turns it into the search's error.
    assert issubclass(common.ProxyUnusable, common.CollectorSetupFailed)
    assert issubclass(common.AssumeRoleFailed, common.CollectorSetupFailed)
