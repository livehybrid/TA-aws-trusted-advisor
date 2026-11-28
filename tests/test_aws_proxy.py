"""botocore_proxies: the Proxy tab's stanza as botocore's Config(proxies=...).

Pure function, no Splunk. The end-to-end proofs are tests/test_proxy.py,
tests/test_socks.py and tests/test_proxy_fail_closed.py; this pins the cases
they do not run (remote DNS, every refusal reason, every way of saying "off").

Fails closed: {} (no proxy) only when Enable is explicitly off. Everything
else that does not give a usable proxy raises ProxySettingsError.
"""
from __future__ import annotations

import os
import sys
import urllib.parse

import pytest

sys.dont_write_bytecode = True  # never leave .pyc under package/bin
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "package", "bin"))

from aws_proxy import ProxySettingsError, botocore_proxies  # noqa: E402

ON = {"proxy_enabled": "1", "proxy_type": "http", "proxy_url": "proxy.corp.example", "proxy_port": "3128",
      "proxy_username": "", "proxy_password": "", "proxy_rdns": ""}
# The stanza exactly as ucc-gen writes it to default/ta_aws_trusted_advisor_settings.conf.
AS_SHIPPED = {"proxy_enabled": "", "proxy_type": "http", "proxy_url": "", "proxy_port": "",
              "proxy_username": "", "proxy_password": "", "proxy_rdns": ""}


def test_both_schemes_point_at_the_proxy():
    got = botocore_proxies({"proxy_url": "proxy.corp.example", "proxy_port": "3128", "proxy_type": "http"})
    assert got == {"http": "http://proxy.corp.example:3128", "https": "http://proxy.corp.example:3128"}


def test_credentials_are_quoted_and_round_trip():
    got = botocore_proxies({"proxy_enabled": "1", "proxy_url": "p", "proxy_port": "1",
                            "proxy_username": "a b", "proxy_password": "x@y:z/%"})
    parts = urllib.parse.urlsplit(got["https"])
    assert (urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password)) == ("a b", "x@y:z/%")
    assert parts.hostname == "p" and parts.port == 1


@pytest.mark.parametrize("enabled", ["", "0", "false", "False", "no", "off", False, None])
def test_explicitly_off_means_no_proxy(enabled):
    # Host and port still filled in, as after unticking Enable on the tab.
    assert botocore_proxies({**ON, "proxy_enabled": enabled}) == {}


def test_never_configured_means_no_proxy():
    assert botocore_proxies(AS_SHIPPED) == {}


@pytest.mark.parametrize("settings,reason", [
    (None, "no proxy settings"),
    ({}, "no host"),  # no proxy_enabled counts as enabled
    ({**ON, "proxy_url": ""}, "no host"),
    ({**ON, "proxy_url": "  "}, "no host"),
    ({**ON, "proxy_url": "http://proxy"}, "not a host"),
    ({**ON, "proxy_url": "user@proxy"}, "not a host"),
    ({**ON, "proxy_port": ""}, "no port"),
    ({**ON, "proxy_port": "abc"}, "1 to 65535"),
    ({**ON, "proxy_port": "0"}, "1 to 65535"),
    ({**ON, "proxy_port": "65536"}, "1 to 65535"),
    ({**ON, "proxy_type": "ftp"}, "'ftp' is not supported"),
    ({**ON, "proxy_username": "u"}, "user name but no password"),
    ({**ON, "proxy_password": "p"}, "password but no user name"),
    ({**ON, "proxy_enabled": "maybe"}, "neither on nor off"),
])
def test_enabled_but_unusable_refuses(settings, reason):
    with pytest.raises(ProxySettingsError) as e:
        botocore_proxies(settings)
    assert reason in str(e.value) and "Proxy settings unusable" in str(e.value)


def test_socks_types_give_socks_urls():
    base = {"proxy_url": "socks.corp.example", "proxy_port": "1080", "proxy_rdns": "0"}
    assert botocore_proxies({**base, "proxy_type": "socks5"})["https"] == "socks5://socks.corp.example:1080"
    assert botocore_proxies({**base, "proxy_type": "socks4"})["http"] == "socks4://socks.corp.example:1080"


def test_remote_dns_lets_the_proxy_resolve():
    base = {"proxy_url": "s", "proxy_port": "1080", "proxy_rdns": "1"}
    assert botocore_proxies({**base, "proxy_type": "socks5"})["https"] == "socks5h://s:1080"
    assert botocore_proxies({**base, "proxy_type": "socks4"})["https"] == "socks4a://s:1080"
    # Meaningless for an HTTP proxy, which always resolves the target itself.
    assert botocore_proxies({**base, "proxy_type": "http"})["https"] == "http://s:1080"
