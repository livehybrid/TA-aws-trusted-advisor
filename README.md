![Splunkbase Downloads](https://img.shields.io/endpoint?url=https%3A%2F%2Fsplunkbasebadge.livehybrid.com%2Fv1%2Fdownloads%2F4207?1)  ![Splunkbase Installs](https://img.shields.io/endpoint?logo=icloud&url=https%3A%2F%2Fsplunkbasebadge.livehybrid.com%2Fv1%2Fsplunkcloud%2F4207?2)  ![Splunkbase AppInspect](https://img.shields.io/endpoint?url=https%3A%2F%2Fsplunkbasebadge.livehybrid.com%2Fv1%2Fappinspect%2F4207?1)  ![Splunkbase Compatibility](https://img.shields.io/endpoint?url=https%3A%2F%2Fsplunkbasebadge.livehybrid.com%2Fv1%2Flatest_compat%2F4207) 

# AWS Trusted Advisor Aggregator

Version 1.2.0 (Released 27th Nov 2024) . 

## Compatibility

| Attribute | Value |
|-----------|-------|
| **Python runtime** | 3.13 (`python.required = 3.13` on the input and the `getchecks` search command) |
| **Supported** | Splunk Enterprise and Cloud 10.4 and later |
| **Tested in CI** | Real Splunk 10.4 and the newest 10.6 in Docker (collector, proxies, browser tests) + AppInspect `cloud`, `future`, `private_victoria` on every push |
| **Deployment roles** | Standalone, Distributed, Search Head Clustering |

Every supported Splunk honours `python.required = 3.13`, so the add-on runs on
Python 3.13; CI checks that splunkd launched every input on 3.13. New releases
support Splunk 10.4 and later. For an older Splunk, use the last release that
supported it.

### Bug Fixes  
1. #57 - Deduping removes some account data  
1. General Library updates
1. Repo tidy

## Intro

This app allows you to configure AWS Trusted Advisor reporting directly into Splunk.
AWS Trusted Advisor help you reduce cost, increase performance, and improve security by optimising your AWS environment and is included in Business and Enterprise support packages.

AWS Basic Support and AWS Developer Support customers get access to 6 security checks (S3 Bucket Permissions, Security Groups - Specific Ports Unrestricted, IAM Use, MFA on Root Account, EBS Public Snapshots, RDS Public Snapshots) and 50 service limit checks. AWS Business Support and AWS Enterprise Support customers get access to all 115 Trusted Advisor checks (14 cost optimization, 17 security, 24 fault tolerance, 10 performance, and 50 service limits) and recommendations. [More Info on Trusted Advisor](https://aws.amazon.com/premiumsupport/technology/trusted-advisor/). 

Using this app you can configure a number of AWS accounts with ease and a pre-build dashboard displays your Trusted Advisor recommendations in one place, for all accounts, with the option of breaking down per account. 


Credit to Hurricane Labs who built the foundations to this app which included data collection and dashboard.  

[Check out more information on Trusted Advisor and the capabilities](https://aws.amazon.com/premiumsupport/ta-iam/)
#### How the app works
AWS Basic Support and AWS Developer Support customers get access to 6 security checks (S3 Bucket Permissions, Security Groups - Specific Ports Unrestricted, IAM Use, MFA on Root Account, EBS Public Snapshots, RDS Public Snapshots) and 50 service limit checks. AWS Business Support and AWS Enterprise Support customers get access to all 115 Trusted Advisor checks (14 cost optimization, 17 security, 24 fault tolerance, 10 performance, and 50 service limits) and recommendations. For a complete list of checks and descriptions, explore Trusted Advisor Best Practices. 


## Installation
#### Requirements
* The app requires AWS credentials in order to collect data from AWS Trusted Advisor, this can be through the use of an Instance Profile, IAM User and/or IAM Role.
* The AWS Credentials configured must have at least the following Permissions in order to query Trusted Advisor
```
{
       "Version": "2012-10-17",
       "Statement": [
           {
               "Effect": "Allow",
               "Action": "trustedadvisor:Describe*",
               "Resource": "*"
           }
       ]
   }
```

#### Docker deployment
This app has been tested in a standalone container using the following command:  
``docker run -d -v $(pwd):/opt/splunk/etc/apps/TA-aws-trusted-advisor -p 8009:8000 -e "SPLUNK_START_ARGS=--accept-license" -e "SPLUNK_PASSWORD=MyPassword1" --name splunktaaws splunk/splunk:latest``  
 
 During testing, varying AWS IAM configurations were tested, including:
 * Standalone IAM User (Access/Secret Key)  
 * Assume Role using IAM User (Access/Secret Key + Role ARN)
 * Assume Role using Instance Profile (when running from an AWS EC2 host with appropriate credentials)  
 
The app has been tested on the latest 8.0 and 8.1 Splunk Enterprise builds.

#### Installation on existing deployments

The app was designed to be installed on an All-In-One (AIO) instance, or onto your Search Head(s), however alternative configurations are possible.

The app does not need installing on your indexing tier.

It is possible to run the modular input section of the app on a heavy-forwarder or an [IDM](https://www.splunk.com/en_us/blog/platform/introducing-inputs-data-manager-on-splunk-cloud.html), however you will also need to install the app on your search head(s).

## Configuration

1. Once installed, navigate to the app using the dropdown at the top of the Splunk UI.  
<img src="package/appserver/static/img/screenshot-app-dropdown.png" width="200" />

2. Navigate to the "Inputs" tab.  

3. Create a new input:  
*Name*: Something to identify your this input from any other AWS accounts you are monitoring  
*Interval*: How often to scrape the Trusted Advisor check results, recommended value 43200 (every 12 hours)  
*Index*: The name of the index that you want the scraped data to go into. Note: the `trusted-advisor-index` macro will need updating to match your chosen index (index=main by default).  
*AWS Access Key*: The Access Key of the IAM user to use to connect, not required if you're running on your Splunk instance on AWS using an [Instance Profile](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-ec2_instance-profiles.html)  
*AWS Secret Key*: As above  
*Role ARN*: Set this to an AWS IAM Role ARN if your IAM User/Instance Profile is required to assume a different role to access Trusted Advisor (e.g. accessing other accounts).  
If the role cannot be assumed, that input logs `Could not assume role_arn=...` and collects nothing for the run. It does not fall back to the access keys or instance profile, which would collect as a different principal. `| getchecks` behaves the same way: it stops with that error in the search.  
<img src="package/appserver/static/img/screenshot-app-config-input.png" width="200" />

4. Enable the saved search "Trusted Advisor Checks Lookup Populator" - This is used to generate the metadata used in the dashboards to display the various Trusted Advisor checks.  
<img src="package/appserver/static/img/screenshot-savedsearch-config.png" width="200" />

5. Manually run the saved search or manually run `| getchecks | outputlookup trusted_advisor_checks`  
<img src="package/appserver/static/img/screenshot-lookup-populator.png" width="200" />

6. If you are using a custom index (default is `main`), update the `trusted-advisor-index` macro.  

### Proxy
Configuration > Proxy sends every AWS call (STS and Support, from the input and from `| getchecks`) through an HTTP, SOCKS4 or SOCKS5 proxy. Tick "Remote DNS resolution" to have a SOCKS proxy resolve the AWS host names.

Proxy handling fails closed. The add-on connects directly only when Enable is unticked (or the Proxy tab has never been saved). When Enable is ticked but the settings are incomplete or invalid (no host or port, a port outside 1 to 65535, an unsupported type, a user name without a password or the reverse), or when the proxy settings cannot be read at all (for example a stored password that cannot be decrypted), the input and `| getchecks` make no AWS call at all, not even a direct one. The input logs `input=<name> Proxy settings unusable: <reason>` to `ta-aws-trusted-advisor_input.log`, and `| getchecks` stops with the same error in the search.

### Custom AWS endpoint (advanced, optional)
By default the add-on calls the public AWS endpoints. To route it through a VPC interface endpoint or an internal gateway, create `local/ta_aws_trusted_advisor_endpoints.conf`:

```ini
[support]
endpoint_url = https://vpce-0123abcd.support.us-east-1.vpce.amazonaws.com
```

Stanzas are boto3 service names (`support`, `sts`). It is a conf file rather than an environment variable because Splunk modular inputs do not inherit splunkd's environment. See `README/ta_aws_trusted_advisor_endpoints.conf.spec`. The add-on's CI uses the same setting to run the collector end to end against a mock AWS Support API (`docker/mock/mock_aws.py`, asserted by `tests/test_mock_aws_data.py`).

## Operation
Once configured, navigate to the `Trusted Advisor` tab, this will display the pre-configured dashboard giving an overview of your Trusted Advisor findings.  
Use the Time and Account dropdowns to select different time periods, or to view specific (or all) AWS accounts. The names in the account list correspond to the input(s) created during the configuration of the app. 

![screenshot3](package/appserver/static/img/screenshot-app-usage.png)

Once you see your recommendations, click a row in the Overview table to see more information, e.g. specific resource recommendations or at-risk components.
## Troubleshooting
If you are not seeing results in your dashboard, there are a couple of things to check.

1. Check that the `trusted_advisor_checks` lookup is not empty:
![screenshot7](package/appserver/static/img/screenshot-test-lookup.png)
If this is empty, try running the "Populator" saved search, or manually run `| getchecks | outputlookup trusted_advisor_checks` and use the job inspector to identify any errors.

2. Check that the `trusted-advisor-index` macro is referencing the index that you are storing your data in. If there is no data in the expected index, check the input is enabled and the details are correct. Failing this, check the _internal logs (see below).

3. Internal logs - Check `index=_internal source=*ta-aws-trusted-advisor*` for any errors (the collector logs to `$SPLUNK_HOME/var/log/splunk/ta-aws-trusted-advisor_input.log`). A refused role, bad credentials or unusable proxy settings are logged there with `input=<name>`.  
The most common cause of error is that the IAM User credentials are incorrect or the User/Role/Profile does not have the required permissions.

4. Manually execute the modular input to check if the script runs correctly:
`$SPLUNK_HOME/bin/splunk cmd splunkd print-modinput-config aws_trusted_advisor aws_trusted_advisor://<YOUR_INPUT_NAME> | $SPLUNK_HOME/bin/splunk cmd python3 $SPLUNK_HOME/etc/apps/TA-aws-trusted-advisor/bin/aws_trusted_advisor.py`

5. If you're still experiencing any issues, please [raise an issue in GitHub](https://github.com/livehybrid/TA-aws-trusted-advisor/issues) 
