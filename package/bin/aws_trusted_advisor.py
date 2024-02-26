import import_declare_test

import sys
import json

from splunklib import modularinput as smi
from solnlib import conf_manager
from splunktaucclib.splunk_aoblib.setup_util import Setup_Util
import splunk.Intersplunk as si
from solnlib import log
from solnlib import credentials
from common import *


class AWS_TRUSTED_ADVISOR(smi.Script):
    def __init__(self):
        self.appName = "TA-aws-trusted-advisor"
        self.confName = "ta_aws_trusted_advisor"
        super(AWS_TRUSTED_ADVISOR, self).__init__()

    def get_scheme(self):
        scheme = smi.Scheme("aws_trusted_advisor")
        scheme.description = "AWS Trusted Advisor"
        scheme.use_external_validation = True
        scheme.streaming_mode_xml = True
        scheme.use_single_instance = False

        scheme.add_argument(smi.Argument("name", title="Name", description="Name", required_on_create=True))

        scheme.add_argument(
            smi.Argument(
                "aws_access_key",
                required_on_create=False,
            )
        )

        scheme.add_argument(
            smi.Argument(
                "aws_secret_key",
                required_on_create=False,
            )
        )

        scheme.add_argument(
            smi.Argument(
                "role_arn",
                required_on_create=False,
            )
        )

        return scheme

    def validate_input(self, definition):
        return

    def get_log_level(self, logger):
        """
        This function returns the log level for the addon from configuration file.
        :param session_key: session key for particular modular input.
        :return : log level configured in addon.
        """
        try:
            settings_cfm = conf_manager.ConfManager(
                self.session_key,
                self.appName,
                realm="__REST_CREDENTIAL__#{}#configs/conf-{}_settings".format(self.appName, self.confName),
            )

            logging_details = settings_cfm.get_conf(self.confName + "_settings").get("logging")

            log_level = logging_details.get("loglevel") if (logging_details.get("loglevel")) else "INFO"
            return log_level

        except Exception:
            logger.error("Failed to fetch the log details from the configuration taking INFO as default level.")
            return "INFO"

    def get_account_credentials(self, input_name):
        realm = f"__REST_CREDENTIAL__#TA-aws-trusted-advisor#data/inputs/aws_trusted_advisor"
        creds = credentials.CredentialManager(
            self.session_key,
            self.appName,
            realm=realm,
        )
        return creds.get_password(input_name)

    def stream_events(self, inputs, ew):
        self.context_meta = inputs.metadata
        self.session_key = inputs.metadata["session_key"]
        self.splunk_uri = inputs.metadata["server_uri"]
        self.setup_util = Setup_Util(self.splunk_uri, self.session_key)
        self.restPath = "ta_aws_trusted_advisor"

        for input_name, input_item in inputs.inputs.items():
            self.input_item = input_item
            self.input_item["name"] = input_name
            # Generate logger with input name
            _, input_name = input_name.split("//", 2)
            logger = log.Logs().get_logger("{}_input".format(self.appName))

            # Log level configuration
            log_level = self.get_log_level(logger)
            logger.setLevel(log_level)
            self.logger = logger
            try:
                aws_credentials = json.loads(self.get_account_credentials(input_name=input_name))
                aws_client = aws_authenticate(
                    self,
                    aws_credentials["aws_access_key"],
                    aws_credentials["aws_secret_key"],
                    role_arn=input_item.get("role_arn", None),
                )
            except:
                aws_client = aws_authenticate(self, None, None, role_arn=input_item.get("role_arn", None))

            checks = get_trusted_advisor_checks(aws_client)
            loop_checks(aws_client, checks, self, ew)


if __name__ == "__main__":
    exit_code = AWS_TRUSTED_ADVISOR().run(sys.argv)
    sys.exit(exit_code)
