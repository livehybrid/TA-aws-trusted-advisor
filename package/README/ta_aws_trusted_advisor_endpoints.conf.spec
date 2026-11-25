# Optional AWS endpoint overrides for TA-aws-trusted-advisor.
#
# Not on the Configuration page and not needed by most installs: with no
# ta_aws_trusted_advisor_endpoints.conf at all (the default) the add-on calls
# the public AWS endpoints, exactly as before. Use it to route the add-on
# through a VPC interface endpoint or an internal gateway. The add-on's own CI
# uses it to point the collector at a mock AWS service.
#
# Put overrides in
# $SPLUNK_HOME/etc/apps/TA-aws-trusted-advisor/local/ta_aws_trusted_advisor_endpoints.conf,
# one stanza per AWS service the add-on calls: support, sts.

[<service>]
endpoint_url = <string>
* An http:// or https:// URL boto3 uses instead of the public endpoint for
  this service, for example https://vpce-0123abcd.support.us-east-1.vpce.amazonaws.com
* Leave unset (or empty) to use AWS.
* Default: not set
