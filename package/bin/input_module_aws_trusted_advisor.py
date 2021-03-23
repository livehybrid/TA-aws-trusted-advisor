# encoding = utf-8

import os
import sys
import time
import datetime
import json
from collections import OrderedDict
import boto3
#import common
from botocore.exceptions import EndpointConnectionError
from botocore.exceptions import ClientError
from splunk.clilib import cli_common as cli


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

    check_metadata.append("-") # some are missing a header?
    for row in container:
        for h, v in zip(check_metadata, row):
            header = h or "-"
            value = v or " "
            if isinstance(value, (list,)):
                value = ", ".join(value)
            meta = header + ": " + value + ","
            if 'Green' in value or 'Yellow' in value or 'Red' in value:
                header = 'Status'
                meta_html = '<p class="' + value.lower() + ' status">' + '<b>' + header + \
                            ':</b> ' + value + '</p>'
            else:
                meta_html = '<p>' + '<b>' + header + ':</b> ' + value + '</p>'
            merged.append(meta)
            merged_html.append(meta_html)
        merged.append('---')
        merged_html.append('---')
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
            if k_flagged == 'metadata':
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
    """ Check if timestamp is newer """
    checkpoint = checkpoint.split('T')[0]
    timestamp = timestamp.split('T')[0]
    checkpoint_dt = datetime.datetime.strptime(checkpoint, '%Y-%m-%d')
    timestamp_dt = datetime.datetime.strptime(timestamp, '%Y-%m-%d')
    return bool(timestamp_dt > checkpoint_dt)


def validate_input(helper, definition):
    """Implement your own validation logic to validate the input stanza configurations"""
    # This example accesses the modular input variable
    # input_friendly_name = definition.parameters.get('input_friendly_name', None)
    # profile = definition.parameters.get('profile', None)
    pass


def authenticate(helper):
    """
    Authenticates against AWS
    :return: aws_client
    """
    
    aws_access_key_id=helper.get_arg('aws_access_key') if helper.get_arg('aws_access_key') !="" else None
    aws_secret_access_key=helper.get_arg('aws_secret_key') if helper.get_arg('aws_secret_key') != "" else None
    aws_session_token=None
    
    role_arn = helper.get_arg('role_arn')
    if role_arn:
        try:
            sts_response = boto3.client(
                'sts',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key
            ).assume_role(
                RoleArn=role_arn,
                RoleSessionName="splunk",
                DurationSeconds=900 #min 900 max inf
            )
            helper.log_info("Created STS Client and assumed role={}".format(role_arn))
            sts_credentials = sts_response['Credentials']
            aws_access_key_id = sts_credentials['AccessKeyId']
            aws_secret_access_key = sts_credentials['SecretAccessKey']
            aws_session_token = sts_credentials['SessionToken']
            helper.log_debug("Assumed role={}".format(role_arn))
        except:
            helper.log_debug(role_arn)
            print("Failed to get session")
      
    region = 'us-east-1'
    try:
        aws_client = boto3.client(
            'support',
            region_name=region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token
        )
        return aws_client
    except EndpointConnectionError as e:
        message = '{}'.format(e)
        helper.log_critical(message)
    except ClientError as e:
        message = '{}'.format(e)
        helper.log_critical(message)


def get_trusted_advisor_checks(client):
    """
    Creates a list of dicts containg check information
    [{checkId: <val>, metadata: <val>}, ...]
    :return: checks
    :param client
    """
    checks = []
    ta_checks = client.describe_trusted_advisor_checks(
        language='en'
    )
    for check in ta_checks["checks"]:
        checks.append({'checkId': check['id'], 'metadata': check['metadata']})
    return checks


def get_check_result(client, check_id):
    """
    Pulls results for a specific check by the checkId
    :param check_id:
    :param client
    :return: result
    """
    result = client.describe_trusted_advisor_check_result(
        checkId=check_id,
        language='en'
    )['result']
    return result



def loop_checks(client, checks, helper, ew):
    """
    Loops through checks; gets results for each check and determines if there is a newer event
    :param checks:
    :param helper:
    :return: None
    """
    helper.log_debug("Looping over checks...")
    for check in checks:
        check_id = check['checkId']
        result = get_check_result(client, check_id)
        if 'timestamp' in result:  # some checks appear not to have a timestamp? ignore if so
            result_timestamp = result['timestamp'].split('T')[0]
            check_timestamp=helper.get_check_point(check_id)
            helper.log_info("check_timestamp={}".format(check_timestamp))
            #if newer_timestamp(check_timestamp, result_timestamp):
            if True:
                generate_events(helper, result, check, ew)
    return




def now():
    """
    Current time in UTC
    :return: now_format
    """
    utc_now = datetime.datetime.utcnow()
    now_format = utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')
    return now_format


def generate_events(helper, result, check, ew):
    """
    Generates events for Splunk
    :param helper
    :param result:
    :param check:
    :return:
    """
    
    event_data = helper.get_input_stanza(input_stanza_name=helper.get_arg('name'))
    
    check_id = check['checkId']
    check_metadata = check['metadata']
    merged = []
    merged_html = []
    ordered_result = OrderedDict()
    if 'timestamp' in result:
        ordered_result['timestamp'] = result['timestamp']
        del result['timestamp']
    else:  # probably overkill -- all checks SHOULD have a timestamp
        now_timestamp = now()
        ordered_result['timestamp'] = now_timestamp
    for key in result:
        if key == 'flaggedResources':
            merged, merged_html = merge_metadata(result[key], check_metadata)
        if merged:
            ordered_result['metadata'] = merged
            ordered_result['metadata_html'] = merged_html
        ordered_result[key] = result[key]
    event = helper.new_event(json.dumps(ordered_result), source=helper.get_arg('name'), index=event_data['index'], host=event_data['host'], sourcetype=event_data['sourcetype'], done=True, unbroken=True)
    ew.write_event(event)

    helper.save_check_point(check_id, ordered_result['timestamp'])

    return



def collect_events(helper, ew):

    aws_access_key = helper.get_arg('aws_access_key')
    aws_secret_key = helper.get_arg('aws_secret_key')

    role_arn = helper.get_arg('role_arn')
    name = helper.get_arg('name')


    client = authenticate(helper)

    checks = get_trusted_advisor_checks(client)
    helper.log_debug(checks)
    loop_checks(client, checks, helper, ew)
