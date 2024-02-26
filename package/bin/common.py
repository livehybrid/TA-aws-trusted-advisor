""" common.py

Common functions used across both inputs.

"""

import datetime
import logging
import re
import splunk.entity as entity
import splunk.rest
import json
import os, sys, logging as logger
import boto3
from collections import OrderedDict

from splunklib import modularinput as smi
from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import ClientError


def get_session_key():
    """
    Grabs session key from first line of stdin
    :return: session_key
    """
    first_line = sys.stdin.readline().strip()
    session_key = re.sub(r"sessionKey=", "", first_line)
    if session_key is None or session_key == "":
        sys.stderr.write("Please provide a session key for this input to work properly\n")
        sys.exit(0)
    else:
        return session_key


def get_credentials(session_key, owner, namespace, log_level=logger.INFO):
    """
    :param session_key:
    :return: AWS Keys
    """
    logger.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        filename=os.path.join(os.environ["SPLUNK_HOME"], "var", "log", "splunk", "trusted_advisor.log"),
        filemode="a",
    )

    try:
        server_response, server_content = splunk.rest.simpleRequest(
            "/servicesNS/nobody/TA-aws-trusted-advisor/TA_aws_trusted_advisor_aws_trusted_advisor?count=0&output_mode=json",
            sessionKey=session_key,
        )

        if server_response["status"] != "200":
            raise Exception("Server response indicates that the request failed")

        inputs_content = json.loads(server_content)
        inputs = inputs_content["entry"]
        first_input = inputs[0]
        input_name = first_input["name"]
        input_role_arn = first_input["content"]["role_arn"] if "role_arn" in first_input["content"] else ""
        logger.debug("Found input={} with role_arn={}".format(input_name, input_role_arn))
    except Exception as e:
        logger.critical("Could not pull inputs")
        logger.critical(e)
        return None, None, None

    try:
        # list all credentials
        entities = entity.getEntities(
            ["admin", "passwords"],
            namespace=namespace,
            owner=owner,
            sessionKey=session_key,
        )
    except Exception as unknown_exception:
        raise Exception("Could not get %s credentials from splunk. Error: %s" % ("AWS_Trusted_Advisor", str(unknown_exception)))
    # grab first set of credentials
    if entities:
        for i, stanza in entities.items():
            if stanza["eai:acl"]["app"] == namespace:
                cred = json.loads(stanza["clear_password"])
                return cred["aws_access_key"], cred["aws_secret_key"], input_role_arn
    else:
        logger.warning("Unable to find any entities")
        return None, None, input_role_arn


def make_error_message(message, session_key, filename):
    """
    Generates Splunk Error Message
    :param message:
    :param session_key:
    :param filename:
    :return: error message
    """
    logging.error(message)
    splunk.rest.simpleRequest(
        "/services/messages/new",
        postargs={
            "name": "AWS_Trusted_Advisor_for_Splunk",
            "value": "%s - %s" % (filename, message),
            "severity": "error",
        },
        method="POST",
        sessionKey=session_key,
    )


def newer_timestamp(checkpoint, timestamp):
    """Check if timestamp is newer"""
    checkpoint = checkpoint.split("T")[0]
    timestamp = timestamp.split("T")[0]
    checkpoint_dt = datetime.datetime.strptime(checkpoint, "%Y-%m-%d")
    timestamp_dt = datetime.datetime.strptime(timestamp, "%Y-%m-%d")
    return bool(timestamp_dt > checkpoint_dt)


def merge_metadata(result, check_metadata):
    """
    Merging the metadata from checks and description endpoints
    :param result:
    :param check_metadata:
    :return: merged, merged_html
    """
    merged = []
    merged_html = []
    container = get_cleaned_metadata_values(result)

    check_metadata.append("-")  # some are missing a header?
    for row in container:
        for h, v in zip(check_metadata, row):
            header = h or "-"
            value = v or " "
            if isinstance(value, (list,)):
                value = ", ".join(value)
            meta = header + ": " + value + ","
            if "Green" in value or "Yellow" in value or "Red" in value:
                header = "Status"
                meta_html = '<p class="' + value.lower() + ' status">' + "<b>" + header + ":</b> " + value + "</p>"
            else:
                meta_html = "<p>" + "<b>" + header + ":</b> " + value + "</p>"
            merged.append(meta)
            merged_html.append(meta_html)
        merged.append("---")
        merged_html.append("---")
    return merged, merged_html


def get_cleaned_metadata_values(result):
    """
    Goes through metadata values and cleans them, specifically if there are buckets it merges them into one list
    :param result:
    :return: container
    """
    container = []
    for flagged in result:
        for k_flagged, v_flagged in flagged.items():
            if k_flagged == "metadata":
                if v_flagged[0]:
                    container.append(v_flagged)
                else:
                    buckets = container[-1][-1]

                    if buckets is None:
                        buckets = []
                    buckets.append(v_flagged[-1])
                    container[-1][-1] = buckets
    return container


def newer_timestamp(checkpoint, timestamp):
    """Check if timestamp is newer"""
    checkpoint = checkpoint.split("T")[0]
    timestamp = timestamp.split("T")[0]
    checkpoint_dt = datetime.datetime.strptime(checkpoint, "%Y-%m-%d")
    timestamp_dt = datetime.datetime.strptime(timestamp, "%Y-%m-%d")
    return bool(timestamp_dt > checkpoint_dt)


def aws_authenticate(helper, aws_access_key_id=None, aws_secret_access_key=None, role_arn=""):
    """
    Authenticates against AWS
    :return: aws_client
    """
    aws_session_token = None

    if role_arn:
        try:
            sts_response = boto3.client(
                "sts",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            ).assume_role(
                RoleArn=role_arn,
                RoleSessionName="splunk",
                DurationSeconds=900,  # min 900 max inf
            )
            helper.logger.info("Created STS Client and assumed role={}".format(role_arn))
            sts_credentials = sts_response["Credentials"]
            aws_access_key_id = sts_credentials["AccessKeyId"]
            aws_secret_access_key = sts_credentials["SecretAccessKey"]
            aws_session_token = sts_credentials["SessionToken"]
            helper.logger.debug("Assumed role={}".format(role_arn))
        except:
            print("Failed to get session")

    region = "us-east-1"
    try:
        aws_client = boto3.client(
            "support",
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )
        return aws_client
    except EndpointConnectionError as e:
        message = "{}".format(e)
        helper.logger.critical(message)
    except ClientError as e:
        message = "{}".format(e)
        helper.logger.critical(message)


def get_trusted_advisor_checks(client):
    """
    Creates a list of dicts containg check information
    [{checkId: <val>, metadata: <val>}, ...]
    :return: checks
    :param client
    """
    checks = []
    ta_checks = client.describe_trusted_advisor_checks(language="en")
    for check in ta_checks["checks"]:
        checks.append({"checkId": check["id"], "metadata": check["metadata"]})
    return checks


def get_check_result(client, check_id):
    """
    Pulls results for a specific check by the checkId
    :param check_id:
    :param client
    :return: result
    """
    result = client.describe_trusted_advisor_check_result(checkId=check_id, language="en")["result"]
    return result


def loop_checks(client, checks, helper, ew):
    """
    Loops through checks; gets results for each check and determines if there is a newer event
    :param checks:
    :param helper:
    :return: None
    """
    helper.logger.debug("Looping over checks...")
    for check in checks:
        check_id = check["checkId"]
        result = get_check_result(client, check_id)
        generate_events(helper, result, check, ew)
    return


def generate_events(helper, result, check, ew):
    """
    Generates events for Splunk
    :param helper
    :param result:
    :param check:
    :return:
    """

    check_id = check["checkId"]
    check_metadata = check["metadata"]
    merged = []
    merged_html = []
    ordered_result = OrderedDict()
    if "timestamp" in result:
        ordered_result["timestamp"] = result["timestamp"]
        del result["timestamp"]
    else:  # probably overkill -- all checks SHOULD have a timestamp
        now_timestamp = now()
        ordered_result["timestamp"] = now_timestamp
    for key in result:
        if key == "flaggedResources":
            merged, merged_html = merge_metadata(result[key], check_metadata)
        if merged:
            ordered_result["metadata"] = merged
            ordered_result["metadata_html"] = merged_html
        ordered_result[key] = result[key]
    event = smi.Event(
        data=json.dumps(ordered_result),
        time=ordered_result["timestamp"],
        host=helper.input_item["host"],
        index=helper.input_item["index"],
        source=helper.input_item["name"],
        sourcetype=helper.input_item["sourcetype"],
        done=True,
        unbroken=True,
    )
    ew.write_event(event)

    #    helper.save_check_point(check_id, ordered_result["timestamp"])

    return


def now():
    """
    Current time in UTC
    :return: now_format
    """
    utc_now = datetime.datetime.utcnow()
    now_format = utc_now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return now_format
