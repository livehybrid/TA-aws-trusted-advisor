"""
getchecks.py
Pulls in AWS Trusted Advisor checks information.
"""
import splunk.Intersplunk
import splunk.rest
from splunk.clilib import cli_common as cli
import boto3
import common
from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import ClientError


def get_checks(results):
    """
    Custom command to pull in checkId, Name, Category and Description
    :param results:
    :return: Splunk events
    """
    events = []
    row = {}
    for check in results:
        row['id'] = check['id']
        row['name'] = check['name']
        row['category'] = check['category']
        row['description'] = check['description']
        events.append(row)
        row = {}

    return splunk.Intersplunk.outputResults(events)


if __name__ == "__main__":

    splunk_results, unused1, settings = splunk.Intersplunk.getOrganizedResults()
    splunk_session_key = settings.get("sessionKey", None)
    owner              = settings.get("owner", "admin")
    namespace          = settings.get("namespace", "search")
    access_key_id, secret_access_key = common.get_credentials(splunk_session_key, owner, namespace)
    session_token=None
    region = 'us-east-1'
    try:
        client = boto3.client(
            'support',
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token
        )
        checks = client.describe_trusted_advisor_checks(language='en')['checks']
        output=get_checks(checks)
        splunk_results = output

    except EndpointConnectionError as e:
        message = '{}'.format(e)
        common.make_error_message(message, splunk_session_key, 'getchecks.py')
    except ClientError as e:
        message = '{}'.format(e)
        common.make_error_message(message, splunk_session_key, 'getchecks.py')
