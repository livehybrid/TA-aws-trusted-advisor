"""The Proxy tab's settings in the form botocore takes them.

botocore chooses a proxy by the scheme of the URL it is calling and only knows
"http" and "https" keys: Config(proxies={"http": url, "https": url}). Every
real AWS endpoint is https, so a dict keyed by anything else (for example the
proxy type, which is what this add-on used to build) is silently ignored and
the calls go direct.

botocore has no SOCKS support of its own: a socks4/socks5 type gives a socks
URL here (socks4a/socks5h when "Remote DNS resolution" is ticked, so the proxy
resolves the AWS host name), and botocore_socks.install() must have run for
botocore to use it.
"""
from __future__ import annotations

import urllib.parse

SUPPORTED_TYPES = ("http", "https", "socks4", "socks5")
# With remote DNS the proxy, not this host, resolves the AWS host name.
REMOTE_DNS_SCHEMES = {"socks4": "socks4a", "socks5": "socks5h"}


def _is_true(value):
    return str(value).strip().lower() in ("1", "true", "t", "yes", "y")


def botocore_proxies(settings, warn=None):
    """Return the `proxies` dict for botocore.config.Config, or {} for none.

    `settings` is the [proxy] stanza (proxy_enabled, proxy_type, proxy_url,
    proxy_port, proxy_username, proxy_password). A dict without proxy_enabled
    counts as enabled: splunktaucclib's get_proxy() returns {} for a disabled
    proxy and drops the key otherwise.
    """
    if not settings:
        return {}
    if "proxy_enabled" in settings and not _is_true(settings.get("proxy_enabled")):
        return {}
    host = (settings.get("proxy_url") or "").strip()
    if not host:
        return {}
    proxy_type = (settings.get("proxy_type") or "http").strip().lower()
    if proxy_type not in SUPPORTED_TYPES:
        if warn:
            warn("Proxy type {} is not supported for AWS API calls (http, https, socks4 or socks5); "
                 "calling AWS without the proxy".format(proxy_type))
        return {}
    scheme = proxy_type
    if proxy_type in REMOTE_DNS_SCHEMES and _is_true(settings.get("proxy_rdns")):
        scheme = REMOTE_DNS_SCHEMES[proxy_type]
    netloc = host
    port = str(settings.get("proxy_port") or "").strip()
    if port:
        netloc = "{}:{}".format(netloc, port)
    user = settings.get("proxy_username") or ""
    password = settings.get("proxy_password") or ""
    if user and password:
        # botocore unquotes these before building Proxy-Authorization, and
        # botocore_socks before the SOCKS handshake.
        netloc = "{}:{}@{}".format(urllib.parse.quote(user, safe=""),
                                   urllib.parse.quote(password, safe=""), netloc)
    url = "{}://{}".format(scheme, netloc)
    return {"http": url, "https": url}


def describe(proxies):
    """The proxy in use for a log line, without credentials."""
    url = proxies.get("https") if proxies else None
    if not url:
        return "none"
    parts = urllib.parse.urlsplit(url)
    auth = " with credentials" if parts.username else ""
    return "{}://{}{}".format(parts.scheme, parts.hostname + (":{}".format(parts.port) if parts.port else ""), auth)
