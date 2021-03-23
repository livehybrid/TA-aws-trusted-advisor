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

def get_session_key():
    """
    Grabs session key from first line of stdin
    :return: session_key
    """
    first_line = sys.stdin.readline().strip()
    session_key = re.sub(r'sessionKey=', "", first_line)
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
    logger.basicConfig(level=log_level,
                   format='%(asctime)s %(levelname)s %(message)s',
                   filename=os.path.join(os.environ['SPLUNK_HOME'],'var','log','splunk','trusted_advisor.log'),
                   filemode='a')

    try:
        server_response, server_content = splunk.rest.simpleRequest('/servicesNS/nobody/TA-aws-trusted-advisor/TA_aws_trusted_advisor_aws_trusted_advisor?count=0&output_mode=json', sessionKey=session_key)

        if server_response['status'] != '200':
            raise Exception("Server response indicates that the request failed")

        inputs_content = json.loads(server_content)
        inputs = inputs_content['entry']
        first_input = inputs[0]
        input_name = first_input['name']
        input_role_arn = first_input['content']['role_arn']
        logger.debug("Found input={} with role_arn={}".format(input_name, input_role_arn))
    except(Exception) as e:
        logger.critical("Could not pull inputs")
        logger.critical(e)
        return None, None, None

    try:
        # list all credentials
        entities = entity.getEntities(['admin', 'passwords'], namespace=namespace, owner=owner, sessionKey=session_key)
    except Exception as unknown_exception:
        raise Exception("Could not get %s credentials from splunk. Error: %s"
                        % ("AWS_Trusted_Advisor", str(unknown_exception)))
    # grab first set of credentials
    if entities:
        for i,stanza in entities.items():
            if stanza['eai:acl']['app'] == namespace:
                cred = json.loads(stanza['clear_password'])
                return cred['aws_access_key'], cred['aws_secret_key'], input_role_arn
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
        '/services/messages/new',
        postargs={'name': 'AWS_Trusted_Advisor_for_Splunk', 'value': '%s - %s' % (filename, message),
                  'severity': 'error'}, method='POST', sessionKey=session_key
    )

def newer_timestamp(checkpoint, timestamp):
    """ Check if timestamp is newer """
    checkpoint = checkpoint.split('T')[0]
    timestamp = timestamp.split('T')[0]
    checkpoint_dt = datetime.datetime.strptime(checkpoint, '%Y-%m-%d')
    timestamp_dt = datetime.datetime.strptime(timestamp, '%Y-%m-%d')
    return bool(timestamp_dt > checkpoint_dt)
