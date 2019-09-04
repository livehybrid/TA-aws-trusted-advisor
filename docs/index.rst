.. CEF Microsoft Windows Add on for Splunk documentation master file, created by
   sphinx-quickstart on Sun Oct 14 10:14:32 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to CEF Imperva Incapsula Add on for Splunk's documentation!
===================================================================

This add on implements the a Splunk add-on for the Imperva Incapsula service


Requirements
-----------------------------

This add on has index time extractions and must be installed on the indexer or heavy forwarder

- Splunk Enterprise 7.1 or newer
- Splunk Common Information Model 4.11 or newer
- CEF add on for Splunk 0.1.1 or newer


Installation
------------------------------

- Install the add on on each indexer and heavy forwarder
- Install the add on on each search head applicable
- Configure inputs
  - For "syslog" format event use sourcetype=cef:syslog
  - For "plain" format without a syslog header use sourcetype=cef:file


Validation
------------------------------

- Search an expected to contain events from Microsoft Windows
- Verify for the sourcetype "cef" source "CEFEventlog:\*" can be found


Next Steps
------------------------------

Review event data and validate the adequacy of the data and CIM mapping to support your use case


.. toctree::
   :maxdepth: 2
   :caption: Contents:



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
