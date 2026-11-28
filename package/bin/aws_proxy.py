"""The Proxy tab's settings in the form botocore takes them.

botocore chooses a proxy by the scheme of the URL it is calling and only knows
"http" and "https" keys: Config(proxies={"http": url, "https": url}). Every
real AWS endpoint is https, so a dict keyed by anything else (for example the
proxy type, which is what this add-on used to build) is silently ignored and
the calls go direct.

botocore has no SOCKS support of its own: a socks4/socks5 type gives a socks
URL here (socks4a/socks5h when "Remote DNS resolution" is ticked, so the proxy
resolves the AWS host name), and botocore_socks.enable_socks(client) makes each
client use it.

Fails closed. The only way to get "no proxy" is a [proxy] stanza whose Enable
is explicitly off (unticked, or never ticked: the add-on ships the stanza with
proxy_enabled empty). Anything else that does not give a usable proxy (no
settings at all, Enable on with no host or port, an invalid port, an unknown
type, half a set of credentials, an Enable value that is neither on nor off)
raises ProxySettingsError, and the caller must not call AWS at all. Going
direct is exactly what a network that only allows the proxy, or a user who
turned the proxy on, has ruled out.
"""
from __future__ import annotations

import urllib.parse

# Proxy tab type -> URL scheme, without and with remote DNS.
SCHEMES = {
    "http": ("http", "http"),
    "https": ("https", "https"),
    "socks4": ("socks4", "socks4a"),
    "socks5": ("socks5", "socks5h"),
}
SUPPORTED_TYPES = tuple(SCHEMES)
_ON = ("1", "true", "t", "yes", "y", "on")
_OFF = ("", "0", "false", "f", "no", "n", "off")


class ProxySettingsError(ValueError):
    """The proxy is, or may be, enabled but cannot be used: make no call at all."""

    def __init__(self, reason):
        super().__init__(
            "Proxy settings unusable: {}. Nothing is sent without the proxy; fix the Proxy tab, "
            "or untick Enable there to connect directly".format(reason))


def _is_true(value):
    return str(value).strip().lower() in _ON


def _enabled(settings):
    """proxy_enabled as True/False. A stanza without it counts as enabled
    (splunktaucclib's get_proxy() drops the key), so it is checked in full."""
    if "proxy_enabled" not in settings:
        return True
    value = settings["proxy_enabled"]
    if isinstance(value, bool):
        return value
    text = "" if value is None else str(value).strip().lower()
    if text in _ON:
        return True
    if text in _OFF:
        return False
    raise ProxySettingsError("proxy_enabled = {!r} is neither on nor off".format(value))


def proxy_url(settings):
    """The proxy URL, or None only when the proxy is explicitly not enabled.

    `settings` is the [proxy] stanza (proxy_enabled, proxy_type, proxy_url,
    proxy_port, proxy_username, proxy_password, proxy_rdns).
    """
    if not isinstance(settings, dict):
        raise ProxySettingsError("no proxy settings were read")
    if not _enabled(settings):
        return None
    host = str(settings.get("proxy_url") or "").strip()
    if not host:
        raise ProxySettingsError("the proxy is enabled but has no host")
    if any(c.isspace() for c in host) or any(c in host for c in "/@?#"):
        raise ProxySettingsError("the proxy host {!r} is not a host name or address".format(host))
    port = str(settings.get("proxy_port") or "").strip()
    if not port:
        raise ProxySettingsError("the proxy is enabled but has no port")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ProxySettingsError("the proxy port {!r} is not a number from 1 to 65535".format(port))
    proxy_type = str(settings.get("proxy_type") or "http").strip().lower()
    if proxy_type not in SCHEMES:
        raise ProxySettingsError("proxy type {!r} is not supported (use {})".format(
            proxy_type, ", ".join(SUPPORTED_TYPES)))
    user = settings.get("proxy_username") or ""
    password = settings.get("proxy_password") or ""
    if bool(user) != bool(password):
        raise ProxySettingsError("the proxy has a {} but no {}; set both or neither".format(
            *(("user name", "password") if user else ("password", "user name"))))
    netloc = "{}:{}".format(host, port)
    if user:
        # botocore unquotes these before building Proxy-Authorization, and
        # botocore_socks before the SOCKS handshake.
        netloc = "{}:{}@{}".format(urllib.parse.quote(user, safe=""),
                                   urllib.parse.quote(password, safe=""), netloc)
    scheme = SCHEMES[proxy_type][1 if _is_true(settings.get("proxy_rdns")) else 0]
    return "{}://{}".format(scheme, netloc)


def botocore_proxies(settings):
    """The `proxies` dict for botocore.config.Config: {} only when the proxy is
    explicitly off. Raises ProxySettingsError (see the module docstring)."""
    url = proxy_url(settings)
    return {"http": url, "https": url} if url else {}


def describe(proxies):
    """The proxy in use for a log line, without credentials."""
    url = proxies.get("https") if proxies else None
    if not url:
        return "none"
    parts = urllib.parse.urlsplit(url)
    auth = " with credentials" if parts.username else ""
    return "{}://{}{}".format(parts.scheme, parts.hostname + (":{}".format(parts.port) if parts.port else ""), auth)
