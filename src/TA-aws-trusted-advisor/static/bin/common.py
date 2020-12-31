""" common.py

Common functions used across both inputs.

"""
import datetime
import logging
import re
import sys
import splunk.entity as entity
import splunk.rest
import json

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


def get_credentials(session_key, owner, namespace):
    """
    :param session_key:
    :return: AWS Keys
    """
    import os, sys, logging as logger
    logger.basicConfig(level=logger.INFO,
                   format='%(asctime)s %(levelname)s %(message)s',
                   filename=os.path.join(os.environ['SPLUNK_HOME'],'var','log','splunk','trusted_advisor.log'),
                   filemode='a')


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
                return cred['aws_access_key'], cred['aws_secret_key']
    else:
        message = 'No credentials have been found. Please set them up in your AWS console.'
        make_error_message(message, session_key, 'common.py')
        sys.exit(0)


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
