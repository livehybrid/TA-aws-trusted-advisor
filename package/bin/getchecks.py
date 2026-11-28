"""
getchecks.py
Pulls in AWS Trusted Advisor checks information.
"""

import import_declare_test
import splunk.Intersplunk
import splunk.rest
from splunk.clilib import cli_common as cli
import common
import logging as logger
import os

logger.basicConfig(
    level=logger.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    filename=os.path.join(os.environ["SPLUNK_HOME"], "var", "log", "splunk", "ta_aws_trusted_advisor_getchecks.log"),
    filemode="a",
)


def get_checks(results):
    """
    Custom command to pull in checkId, Name, Category and Description
    :param results:
    :return: Splunk events
    """
    events = []
    row = {}
    for check in results:
        row["id"] = check["id"]
        row["name"] = check["name"]
        row["category"] = check["category"]
        row["description"] = check["description"]
        events.append(row)
        row = {}

    return splunk.Intersplunk.outputResults(events)


def fail(message, session_key):
    """Stop the search with this error: in its log, as a Splunk message and as
    the search's own error (not an empty result that looks like "no checks")."""
    logger.error(message)
    try:
        common.make_error_message(message, session_key, "getchecks.py")
    except Exception as e:  # the search error below must still be raised
        logger.error("Could not post the Splunk message: {}".format(e))
    splunk.Intersplunk.generateErrorResults("getchecks: {}".format(message))


if __name__ == "__main__":

    splunk_results, unused1, settings = splunk.Intersplunk.getOrganizedResults()
    splunk_session_key = settings.get("sessionKey", None)
    try:
        # The first input's own keys, picked by realm and input name (not the
        # first secret in the search's app, which may be the proxy password).
        aws_access_key_id, aws_secret_access_key, role_arn = common.get_credentials(splunk_session_key)
        # Built exactly as the modular input builds its client: the Proxy tab's
        # proxy (refusing, never going direct, when it is unusable), and the
        # role's credentials or nothing at all. A refused role used to be
        # logged and the base keys used instead, which the input never does.
        client = common.support_client(splunk_session_key, aws_access_key_id, aws_secret_access_key,
                                       role_arn=role_arn, log=logger)
        checks = client.describe_trusted_advisor_checks(language="en")["checks"]
    except common.CollectorSetupFailed as e:
        fail("{}".format(e), splunk_session_key)
    except Exception as e:  # AWS refused or unreachable, keys unreadable, ...
        fail("{}: {}".format(type(e).__name__, e), splunk_session_key)
    else:
        get_checks(checks)
