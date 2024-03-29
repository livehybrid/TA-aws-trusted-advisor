"""
getchecks.py
Pulls in AWS Trusted Advisor checks information.
"""

import import_declare_test
import splunk.Intersplunk
import splunk.rest
from splunk.clilib import cli_common as cli
import boto3
import common
from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import ClientError
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


if __name__ == "__main__":

    splunk_results, unused1, settings = splunk.Intersplunk.getOrganizedResults()
    splunk_session_key = settings.get("sessionKey", None)
    owner = settings.get("owner", "admin")
    namespace = settings.get("namespace", "search")
    aws_access_key_id, aws_secret_access_key, role_arn = common.get_credentials(splunk_session_key, owner, namespace)
    if aws_access_key_id == "":
        aws_access_key_id = None
    if aws_secret_access_key == "":
        aws_secret_access_key = None
    aws_session_token = None
    region = "us-east-1"
    try:
        if role_arn:
            try:
                audit_sts_client = boto3.client("sts")
                sts_response = boto3.client(
                    "sts", aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key, aws_session_token=aws_session_token
                ).assume_role(
                    RoleArn=role_arn, RoleSessionName="splunk", DurationSeconds=900  # min 900 max inf
                )

                sts_credentials = sts_response["Credentials"]
                aws_access_key_id = sts_credentials["AccessKeyId"]
                aws_secret_access_key = sts_credentials["SecretAccessKey"]
                aws_session_token = sts_credentials["SessionToken"]
                logger.info("Assumed role={}".format(role_arn))

            except Exception as e:
                logger.critical("Could not assume role")
                logger.critical(e)

        client = boto3.client(
            "support", region_name=region, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key, aws_session_token=aws_session_token
        )
        checks = client.describe_trusted_advisor_checks(language="en")["checks"]
        output = get_checks(checks)
        splunk_results = output

    except EndpointConnectionError as e:
        message = "{}".format(e)
        common.make_error_message(message, splunk_session_key, "getchecks.py")
    except ClientError as e:
        message = "{}".format(e)
        common.make_error_message(message, splunk_session_key, "getchecks.py")
