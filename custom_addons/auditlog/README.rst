=========
Audit Log
=========

This module allows the administrator to log user operations performed on
data models such as ``create``, ``read``, ``write`` and ``delete``.

**Table of contents**

.. contents::
   :local:

Usage
=====

Go to Settings / Technical / Audit / Rules to subscribe rules. A rule
defines which operations to log for a given data model.
Then, check logs in the Settings / Technical / Audit / Logs menu. You
can group them by user sessions, date, data model or HTTP requests:
Get the details:
A scheduled action exists to delete logs older than 6 months (180 days)
automatically but is not enabled by default. To activate it and/or
change the delay, go to the Configuration / Technical / Automation /
Scheduled Actions menu and edit the Auto-vacuum audit logs entry:
In case you're having trouble with the amount of records to delete per
run, you can pass the amount of records to delete for one model per run
as the second parameter, the default is to delete all records in one go.

There are two possible groups configured to which one may belong. The
first is the Auditlog User group. This group has read-only access to the
auditlogs of individual records through the View Logs action. The second
group is the Auditlog Manager group. This group additionally has the
right to configure the auditlog configuration rules.

Known issues / Roadmap
======================

   - log only operations triggered by some users (currently it logs all
     users)
   - log read operations does not work on all data models, need
     investigation

Credits
=======

Authors
-------

* ABF OSIELL

Contributors
------------

- Sebastien Alix <sebastien.alix@camptocamp.com>
- Holger Brunn <hbrunn@therp.nl>
- Holden Rehg <holdenrehg@gmail.com>
- Eric Lembregts <eric@lembregts.eu>
- Pieter Paulussen <pieter.paulussen@me.com>
- Alan Ramos <alan.ramos@jarsa.com.mx>
- Stefan Rijnhart <stefan@opener.amsterdam>
- Bhavesh Odedra <bodedra@opensourceintegrators.com>
- Hardik Suthar <hsuthar@opensourceintegrators.com>
- Kitti U. <kittiu@ecosoft.co.th>
- Bogdan Valentin Gabor <valentin.gabor@bt-group.com>
- Dennis Sluijk d.sluijk@onestein.nl
- Adam Heinz <adam.heinz@metricwise.com>

Other credits
-------------

- Icon: built with different icons from the `Oxygen
  theme <https://en.wikipedia.org/wiki/Oxygen_Project>`__ (LGPL)

This copy is bundled with Premier Transport TMS by RLFB (www.riamona.com). RLFB removed the upstream project's badges, issue-tracker and maintainer sections; the code, the licence and the copyright notices are unchanged.
