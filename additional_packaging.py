"""ucc-gen build hook (ucc-gen calls cleanup_output_files() after the build).

Points the add-on's settings endpoint at bin/ucc_settings_list_handler.py,
which wraps the generated <restRoot>_rh_settings.py and fixes the one-request
settings list (splunktaucclib answers it with 500 "name=None"; see that file).

ucc-gen hard-codes the default handler for the settings endpoint, so
globalConfig.json cannot select another one. Only the handlerfile line of
[admin_external:<restRoot>_settings] is changed; the rest of restmap.conf stays
as ucc-gen generated it. The build fails if the stanza or either handler file
is missing, rather than shipping the unfixed endpoint quietly.
"""
import json
import os
import re

HANDLER = "ucc_settings_list_handler.py"


def cleanup_output_files(output_directory, ta_name):
    app = os.path.join(output_directory, ta_name)
    gc_path = os.path.join(app, "appserver", "static", "js", "build", "globalConfig.json")
    with open(gc_path) as f:
        rest_root = json.load(f)["meta"]["restRoot"]

    for name in (HANDLER, "%s_rh_settings.py" % rest_root):
        if not os.path.isfile(os.path.join(app, "bin", name)):
            raise SystemExit("additional_packaging: bin/%s missing from the build" % name)

    restmap = os.path.join(app, "default", "restmap.conf")
    with open(restmap) as f:
        lines = f.read().splitlines(True)

    stanza = "[admin_external:%s_settings]" % rest_root
    in_stanza = False
    patched = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_stanza = stripped == stanza
            continue
        if in_stanza and re.match(r"handlerfile\s*=", stripped):
            lines[i] = "handlerfile = %s\n" % HANDLER
            patched = True
    if not patched:
        raise SystemExit("additional_packaging: no handlerfile in %s of %s" % (stanza, restmap))

    with open(restmap, "w") as f:
        f.writelines(lines)
    print("additional_packaging: %s handlerfile -> %s" % (stanza, HANDLER))
