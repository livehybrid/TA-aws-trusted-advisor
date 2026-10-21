"""REST handler for the add-on's UCC settings endpoint (/<restRoot>_settings).

Wraps the UCC-generated <restRoot>_rh_settings.py, so the field models stay
generated from globalConfig.json, and fixes one thing in it: listing the whole
collection in one request.

splunktaucclib (every release up to at least 8.2.0) answers
GET .../<restRoot>_settings with 500 wrapping "REST Error [404] ... name=None":
RestHandler.all() asks the endpoint for the model of name None, and the
settings endpoint is a MultipleModel, which only knows its named stanzas.
Per-stanza GETs (what the Configuration page makes) never take that path.

So the list is built from the per-stanza read the UI already relies on: one
handler.get() per model, with the same credential handling (encrypted fields
masked unless --cred-- is passed). A stanza missing from the conf is skipped.

Wired in by additional_packaging.py at the repo root, which points the
[admin_external:<restRoot>_settings] restmap stanza at this file at build time.
"""
import import_declare_test  # noqa: F401  (puts the app's lib/ on sys.path)

import glob
import importlib
import logging
import os

from solnlib.utils import is_true
from splunktaucclib.rest_handler import admin_external
from splunktaucclib.rest_handler.admin_external import (
    AdminExternalHandler,
    make_conf_item,
)
from splunktaucclib.rest_handler.error import RestError


def _generated_settings_endpoint():
    """The endpoint from the one UCC-generated *_rh_settings.py beside this file."""
    bin_dir = os.path.dirname(os.path.abspath(import_declare_test.__file__))
    found = sorted(glob.glob(os.path.join(bin_dir, "*_rh_settings.py")))
    if len(found) != 1:
        raise RuntimeError(
            "expected exactly one *_rh_settings.py in %s, found %s" % (bin_dir, found)
        )
    # The module name may contain a hyphen, so it cannot be a plain import.
    return importlib.import_module(os.path.basename(found[0])[:-3]).endpoint


class SettingsHandler(AdminExternalHandler):
    def handleList(self, confInfo):
        if self.callerArgs.id:
            return super().handleList(confInfo)

        decrypt = is_true(self.callerArgs.data.get(self.ACTION_CRED, [False])[0])
        for name in self.endpoint.models:
            try:
                entities = list(self.handler.get(name, decrypt=decrypt))
            except RestError as exc:
                if exc.status == 404:
                    continue
                raise
            for entity in entities:
                make_conf_item(confInfo[entity.name], entity.content, entity.eai)


if __name__ == "__main__":
    logging.getLogger().addHandler(logging.NullHandler())
    admin_external.handle(_generated_settings_endpoint(), handler=SettingsHandler)
