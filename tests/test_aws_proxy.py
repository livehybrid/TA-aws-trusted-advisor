"""botocore_proxies: the Proxy tab's stanza as botocore's Config(proxies=...).

Pure function, no Splunk. The end-to-end proof is tests/test_proxy.py; this
pins the cases it does not run (remote DNS, an unknown type, a disabled stanza,
an empty host). tests/test_socks.py proves SOCKS end to end.
"""
from __future__ import annotations

import os
import sys
import urllib.parse

sys.dont_write_bytecode = True  # never leave .pyc under package/bin
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "package", "bin"))

from aws_proxy import botocore_proxies  # noqa: E402


def test_both_schemes_point_at_the_proxy():
    got = botocore_proxies({"proxy_url": "proxy.corp.example", "proxy_port": "3128", "proxy_type": "http"})
    assert got == {"http": "http://proxy.corp.example:3128", "https": "http://proxy.corp.example:3128"}


def test_credentials_are_quoted_and_round_trip():
    got = botocore_proxies({"proxy_enabled": "1", "proxy_url": "p", "proxy_port": "1",
                            "proxy_username": "a b", "proxy_password": "x@y:z/%"})
    parts = urllib.parse.urlsplit(got["https"])
    assert (urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password)) == ("a b", "x@y:z/%")
    assert parts.hostname == "p" and parts.port == 1


def test_off_or_incomplete_means_no_proxy():
    assert botocore_proxies({}) == {}
    assert botocore_proxies(None) == {}
    assert botocore_proxies({"proxy_enabled": "0", "proxy_url": "p", "proxy_port": "1"}) == {}
    assert botocore_proxies({"proxy_enabled": "1", "proxy_url": " ", "proxy_port": "1"}) == {}


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


def test_unknown_type_is_reported_not_silently_dropped():
    warned = []
    assert botocore_proxies({"proxy_url": "p", "proxy_type": "ftp"}, warn=warned.append) == {}
    assert warned and "ftp" in warned[0]
