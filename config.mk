MAIN_APP      = TA-aws-trusted-advisor

#Name of the license file in the root of the repo
LICENSE_FILE  = license-eula.txt
LICENSE_URL   = https://www.splunk.com/en_us/legal/splunk-software-license-agreement.html

AUTHOR = William Searle
COMPANY = NHS Digital

MAIN_DESCRIPTION = AWS Trusted Advisor Aggregator
MAIN_LABEL = AWS Trusted Advisor

#######################
# NOT CURRENTLY USED! #
#######################

SPLUNKBASE    = https://splunkbase.splunk.com/app/XXX/
REPOSITORY    = https://bitbucket.org/SPLServices/ta-cef-microsoft-windows-for-splunk/
DOCSSITE      = https://seckit.readthedocs.io
PROJECTSITE   = https://bitbucket.org/account/user/SPLServices/projects/SECKITCEF

#Do not change this without good reason
DOCKER_IMAGE   = livehybrid/splunk-appbuilder:latest

#Used by the Copy right tool to place the correct copy right on new files
COPYRIGHT_LICENSE_ARG ?= --license-file buildtools/copyright-header/licenses/SPLUNK.erb
COPYRIGHT_HOLDER ?= $(COMPANY)
COPYRIGHT_YEAR ?= 2019

define rst_prolog
.. |MAIN_LABEL| replace:: $(MAIN_LABEL)
.. |VERSION| replace:: $(VERSION)
.. |RELEASE| replace:: $(VERSION)$(PACKAGE_SLUG)
.. |LICENSE| replace:: $(COPYRIGHT_LICENSE)
.. _Repository: $(REPOSITORY)
.. _SPLUNKBASE: $(SPLUNKBASE)
endef
export rst_prolog
