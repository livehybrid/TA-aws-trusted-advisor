# AWS Trusted Advisor Aggregator

Version 1.0.4 . 

## Intro
This app allows you to configure AWS Trusted Advisor reporting directly into Splunk.
AWS Trusted Advisor help you reduce cost, increase performance, and improve security by optimising your AWS environment and is included in Business and Enterprise support packages.
Using this app you can configure a number of AWS accounts with ease and a pre-build dashboard displays your Trusted Advisor recommendations in one place, for all accounts, with the option of breaking down per account. 

AWS accounts can be configured using AWS Access/Secret keys or can be left blank to use the instance profile if running on AWS, along with an option to AssumeRole to a relevant role with permissions. This easily allows your Splunk instance to assume a role in other accounts for the purpose of data collection relating to AWS Trusted Advisor. 
Note: Assumed roles should have the "support:*" permissions.

Credit to Hurricane Labs who built the foundations to this app which included data collection and dashboard.
