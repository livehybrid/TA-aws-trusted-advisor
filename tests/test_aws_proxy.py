"""botocore_proxies: the Proxy tab's stanza as botocore's Config(proxies=...).

Pure function, no Splunk. The end-to-end proof is tests/test_proxy.py; this
pins the cases it does not run (SOCKS, a disabled stanza, an empty host).
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


def test_socks_is_reported_not_silently_dropped():
    warned = []
    assert botocore_proxies({"proxy_url": "p", "proxy_type": "socks5"}, warn=warned.append) == {}
    assert warned and "socks5" in warned[0]
