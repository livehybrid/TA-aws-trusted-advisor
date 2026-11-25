"""Optional AWS endpoint override, read from this app's own conf files.

Default behaviour is unchanged: with no override configured, boto3 resolves
the real AWS endpoint as it always has.

An override exists for two cases: an enterprise network that reaches AWS
through a VPC interface endpoint or gateway, and the add-on's CI, which points
the collector at a mock AWS service. It lives in a conf file rather than an
environment variable because Splunk modular inputs do not inherit splunkd's
container environment. It is deliberately not on the Configuration page.

    $SPLUNK_HOME/etc/apps/<this app>/local/ta_aws_trusted_advisor_endpoints.conf
    [<boto3 service name, e.g. support>]
    endpoint_url = https://vpce-0123.support.eu-west-2.vpce.amazonaws.com

See README/ta_aws_trusted_advisor_endpoints.conf.spec.
"""
from __future__ import annotations

import configparser
import os

CONF = "ta_aws_trusted_advisor_endpoints"
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def endpoint_url(service, app_dir=None):
    """Return the configured endpoint_url for a boto3 service name, or None."""
    app_dir = app_dir or _APP_DIR
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    # local/ after default/ so local wins, as in Splunk's own precedence.
    parser.read([os.path.join(app_dir, d, CONF + ".conf") for d in ("default", "local")])
    if not parser.has_section(service):
        return None
    value = parser.get(service, "endpoint_url", fallback="").strip()
    if not value:
        return None
    if not value.startswith(("https://", "http://")):
        raise ValueError(f"{CONF}.conf [{service}] endpoint_url must start with http:// or https://")
    return value
