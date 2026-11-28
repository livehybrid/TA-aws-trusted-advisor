"""SOCKS proxies for botocore clients, one client at a time.

botocore has no SOCKS support. Given Config(proxies={"https": "socks5://..."})
it rewrites the URL to "http://socks5://..." (ProxyConfiguration._fix_proxy_url)
and hands it to urllib3's HTTP proxy manager, so the call fails. This module
teaches one client's HTTP session to use urllib3's SOCKSProxyManager instead.

Use:

    client = boto3.client("health", config=Config(proxies=proxies), ...)
    botocore_socks.enable_socks(client)   # no-op unless a proxy is socks*

Scope: nothing global is patched. enable_socks() changes the class of that
client's own URLLib3Session (and its ProxyConfiguration) to a subclass that
differs only for socks4/socks4a/socks5/socks5h proxy URLs; http and https
proxies take botocore's own code unchanged. It is idempotent.

It relies on botocore internals that have been stable for years (checked on
1.29 to 1.44): client._endpoint.http_session, URLLib3Session._proxy_config,
_proxy_managers, _get_pool_manager_kwargs, _get_proxy_manager,
_get_request_target, _path_url and ProxyConfiguration._fix_proxy_url. Before
adapting a client it checks those are there and that a socks URL now resolves
to a SOCKSProxyManager; if not it raises BotocoreSocksError rather than let the
call go anywhere else. A socks URL that botocore handled itself would fail to
connect, never go direct, so the failure mode is closed either way.

Needs PySocks (the `socks` module) importable: urllib3.contrib.socks imports it.

Proxy URL schemes (urllib3's meaning):
    socks5   SOCKS5, the client resolves the target host name
    socks5h  SOCKS5, the proxy resolves it ("remote DNS")
    socks4   SOCKS4, client-side resolution
    socks4a  SOCKS4a, the proxy resolves it
Credentials go in the URL percent-encoded (urllib.parse.quote(x, safe="")) and
are decoded here before they reach PySocks; urllib3 itself would not decode
them. SOCKS4 has no password: the user name is sent as the SOCKS4 user id.

Copied as-is between add-ons (TA-aws-health, TA-aws-trusted-advisor); keep it
free of add-on specifics.
"""
from __future__ import annotations

import urllib.parse

SOCKS_SCHEMES = ("socks4", "socks4a", "socks5", "socks5h")

_SESSION_ATTRS = ("_get_pool_manager_kwargs", "_get_proxy_manager", "_get_request_target",
                  "_get_connection_manager", "_path_url", "send")
_CONFIG_ATTRS = ("_fix_proxy_url", "proxy_url_for")

_classes = None


class BotocoreSocksError(RuntimeError):
    """This botocore cannot be taught SOCKS safely; the client was not adapted."""


def is_socks_url(url):
    if not isinstance(url, str) or "://" not in url:
        return False
    return url.split("://", 1)[0].strip().lower() in SOCKS_SCHEMES


def _socks_credentials(proxy_url):
    parts = urllib.parse.urlsplit(proxy_url)
    user = urllib.parse.unquote(parts.username) if parts.username else None
    password = urllib.parse.unquote(parts.password) if parts.password else None
    return user, password


def _build_classes():
    global _classes
    if _classes is not None:
        return _classes
    try:
        from botocore.httpsession import ProxyConfiguration, URLLib3Session
        from urllib3.contrib.socks import SOCKSProxyManager
    except ImportError as e:
        raise BotocoreSocksError("SOCKS proxy needs botocore and urllib3.contrib.socks "
                                 "(PySocks): {}".format(e)) from e
    missing = [a for a in _SESSION_ATTRS if not callable(getattr(URLLib3Session, a, None))]
    missing += [a for a in _CONFIG_ATTRS if not callable(getattr(ProxyConfiguration, a, None))]
    if missing:
        raise BotocoreSocksError("this botocore lacks {}; SOCKS support needs updating".format(
            ", ".join(missing)))

    class SocksProxyConfiguration(ProxyConfiguration):
        def _fix_proxy_url(self, proxy_url):
            if is_socks_url(proxy_url):
                return proxy_url
            return super()._fix_proxy_url(proxy_url)

    class SocksURLLib3Session(URLLib3Session):
        def _get_proxy_manager(self, proxy_url):
            if not is_socks_url(proxy_url):
                return super()._get_proxy_manager(proxy_url)
            manager = self._proxy_managers.get(proxy_url)
            if manager is None:
                user, password = _socks_credentials(proxy_url)
                # SOCKSProxyManager keeps its own SOCKS connection pools; botocore's
                # pool classes would connect straight to the target, so they are
                # deliberately not installed on it.
                manager = SOCKSProxyManager(proxy_url, username=user, password=password,
                                            **self._get_pool_manager_kwargs())
                self._proxy_managers[proxy_url] = manager
            return manager

        def _get_request_target(self, url, proxy_url):
            # A SOCKS proxy only relays bytes: the server gets an origin-form
            # target (path), as it would without a proxy.
            if is_socks_url(proxy_url):
                return self._path_url(url)
            return super()._get_request_target(url, proxy_url)

    _classes = (URLLib3Session, ProxyConfiguration, SocksURLLib3Session, SocksProxyConfiguration,
                SOCKSProxyManager)
    return _classes


def _self_check(session, proxies):
    """The adapted session must route each socks URL to a SOCKSProxyManager."""
    socks_manager = _build_classes()[4]
    for scheme, url in proxies.items():
        if not is_socks_url(url):
            continue
        target = "{}://botocore-socks.invalid/".format(scheme)
        resolved = session._proxy_config.proxy_url_for(target)
        if resolved != url:
            raise BotocoreSocksError("proxy for {} resolved to a rewritten URL".format(scheme))
        if not isinstance(session._get_connection_manager(target, resolved), socks_manager):
            raise BotocoreSocksError("proxy for {} did not get a SOCKS connection manager".format(scheme))


def enable_socks(client):
    """Make `client` honour socks* URLs in its Config(proxies=...).

    Returns True when the client has a SOCKS proxy (and now uses it), False
    when it has none (nothing is changed). Raises BotocoreSocksError when this
    botocore cannot be adapted.
    """
    proxies = dict(getattr(client.meta.config, "proxies", None) or {})
    if not any(is_socks_url(u) for u in proxies.values()):
        return False
    base_session, base_config, socks_session, socks_config, _ = _build_classes()
    endpoint = getattr(client, "_endpoint", None)
    session = getattr(endpoint, "http_session", None)
    config = getattr(session, "_proxy_config", None)
    if session is None or config is None or not hasattr(session, "_proxy_managers"):
        raise BotocoreSocksError("this botocore client has no URLLib3Session to adapt")
    if type(session) is base_session:
        session.__class__ = socks_session
    elif not isinstance(session, socks_session):
        raise BotocoreSocksError("unexpected HTTP session {}".format(type(session).__name__))
    if type(config) is base_config:
        config.__class__ = socks_config
    elif not isinstance(config, socks_config):
        raise BotocoreSocksError("unexpected proxy configuration {}".format(type(config).__name__))
    _self_check(session, proxies)
    return True
