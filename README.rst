apt-mirror-updater: Automated Debian/Ubuntu mirror selection
============================================================

.. image:: https://travis-ci.org/xolox/python-apt-mirror-updater.svg?branch=master
   :target: https://travis-ci.org/xolox/python-apt-mirror-updater

.. image:: https://coveralls.io/repos/xolox/python-apt-mirror-updater/badge.svg?branch=master
   :target: https://coveralls.io/r/xolox/python-apt-mirror-updater?branch=master

The `apt-mirror-updater` package automates robust apt-get_ mirror selection for
Debian_ and Ubuntu_ by enabling discovery of available mirrors, ranking of
available mirrors, automatic switching between mirrors and robust package list
updating (see features_). It's currently tested on Python 2.6, 2.7, 3.4, 3.5,
3.6 and PyPy (although test coverage is still rather low, see status_).

.. contents::
   :local:

.. _features:

Features
--------

**Discovery of available mirrors**
 Debian_ and Ubuntu_ mirrors are discovered automatically by querying the
 `Debian mirror list <https://www.debian.org/mirror/list>`_ or the `Ubuntu
 mirror list <https://launchpad.net/ubuntu/+archivemirrors>`_ (the applicable
 mirror list is automatically selected based on the current platform).

**Ranking of available mirrors**
 Discovered mirrors are ranked by bandwidth (to pick the fastest mirror) and
 excluded if they're being updated (see `issues with mirror updates`_).

**Automatic switching between mirrors**
 The main mirror configured in ``/etc/apt/sources.list`` can be changed with a
 single command. The new (to be configured) mirror can be selected
 automatically or configured explicitly by the user.

**Robust package list updating**
 Several apt-get_ subcommands can fail if the current mirror is being updated
 (see `issues with mirror updates`_) and `apt-mirror-updater` tries to work
 around this by wrapping ``apt-get update`` to retry on failures and
 automatically switch to a different mirror when it looks like the current
 mirror is being updated (because I've seen such updates take more than 15
 minutes and it's not always acceptable to wait for so long, especially in
 automated solutions).

.. _status:

Status
------

On the one hand the `apt-mirror-updater` package was developed based on quite a
few years of experience in using apt-get_ on Debian_ and Ubuntu_ systems and
large scale automation of apt-get (working on 150+ remote systems). On the
other hand the Python package itself is relatively new: it was developed and
published in March 2016. As such:

.. warning:: Until `apt-mirror-updater` has been rigorously tested I consider
             it a proof of concept (beta software) so if it corrupts your
             system you can't complain that you weren't warned! I've already
             tested it on a variety of Ubuntu systems but haven't found the
             time to set up a Debian virtual machine for testing. Most of the
             logic is exactly the same though. The worst that can happen
             (assuming you trust my judgement ;-) is that
             ``/etc/apt/sources.list`` is corrupted however a backup copy is
             made before any changes are applied, so I don't see how this can
             result in irreversible corruption.

I'm working on an automated test suite but at the moment I'm still a bit fuzzy
on how to create representative tests for the error handling code paths (also,
writing a decent test suite requires a significant chunk of time :-).

Installation
------------

The `apt-mirror-updater` package is available on PyPI_ which means installation
should be as simple as:

.. code-block:: sh

   $ pip install apt-mirror-updater

There's actually a multitude of ways to install Python packages (e.g. the `per
user site-packages directory`_, `virtual environments`_ or just installing
system wide) and I have no intention of getting into that discussion here, so
if this intimidates you then read up on your options before returning to these
instructions ;-).

Usage
-----

There are two ways to use the `apt-mirror-updater` package: As the command line
program ``apt-mirror-updater`` and as a Python API. For details about the
Python API please refer to the API documentation available on `Read the Docs`_.
The command line interface is described below.

.. contents::
   :local:

.. A DRY solution to avoid duplication of the `apt-mirror-updater --help' text:
..
.. [[[cog
.. from humanfriendly.usage import inject_usage
.. inject_usage('apt_mirror_updater.cli')
.. ]]]

**Usage:** `apt-mirror-updater [OPTIONS]`

The apt-mirror-updater program automates robust apt-get mirror selection for
Debian and Ubuntu by enabling discovery of available mirrors, ranking of
available mirrors, automatic switching between mirrors and robust package list
updating.

**Supported options:**

.. csv-table::
   :header: Option, Description
   :widths: 30, 70


   "``-r``, ``--remote-host=SSH_ALIAS``","Operate on a remote system instead of the local system. The ``SSH_ALIAS``
   argument gives the SSH alias of the remote host. It is assumed that the
   remote account has root privileges or password-less sudo access."
   "``-f``, ``--find-current-mirror``","Determine the URL of the main mirror that is currently configured in
   /etc/apt/sources.list."
   "``-l``, ``--list-mirrors``",List available (ranked) mirrors on the terminal in a human readable format.
   "``-c``, ``--change-mirror=MIRROR_URL``",Update /etc/apt/sources.list to use the given ``MIRROR_URL``.
   "``-a``, ``--auto-change-mirror``","Discover available mirrors, rank the mirrors by connection speed and update
   status and update /etc/apt/sources.list to use the best available mirror."
   "``-u``, ``--update``, ``--update-package-lists``","Update the package lists using ""apt-get update"", retrying on failure and
   automatically switch to a different mirror when it looks like the current
   mirror is being updated."
   "``-x``, ``--exclude=PATTERN``","Add a pattern to the mirror selection blacklist. ``PATTERN`` is expected to be
   a shell pattern (containing wild cards like ""?"" and ""\*"") that is matched
   against the full URL of each mirror."
   "``-m``, ``--max=COUNT``","Don't query more than ``COUNT`` mirrors for their connection status
   (defaults to 50). If you give the number 0 no limit will be applied.
   
   Because Ubuntu mirror discovery can report more than 300 mirrors it's
   useful to limit the number of mirrors that are queried, otherwise the
   ranking of mirrors will take a long time (because over 300 connections
   need to be established)."
   "``-v``, ``--verbose``",Increase logging verbosity (can be repeated).
   "``-q``, ``--quiet``",Decrease logging verbosity (can be repeated).
   "``-h``, ``--help``",Show this message and exit.

.. [[[end]]]

.. _issues with mirror updates:

Issues with mirror updates
--------------------------

Over the past five years my team (`at work`_) and I have been managing a
cluster of 150+ Ubuntu servers, initially using manual system administration
but over time automating ``apt-get`` for a variety of use cases (provisioning,
security updates, deployments, etc.). As we increased our automation we started
running into various transient failure modes of ``apt-get``, primarily with
``apt-get update`` but incidentally also with other subcommands.

The most frequent failure that we run into is ``apt-get update`` crapping out
with 'hash sum mismatch' errors (see also `Debian bug #624122`_). When this
happens a file called ``Archive-Update-in-Progress-*`` can sometimes be found
on the index page of the mirror that is being used (see also `Debian bug
#110837`_). I've seen these situations last for more than 15 minutes.

My working theory about these 'hash sum mismatch' errors is that they are
caused by the fact that mirror updates aren't atomic, apparently causing
``apt-get update`` to download a package list whose datafiles aren't consistent
with each other. If this assumption proves to be correct (and also assuming
that different mirrors are updated at different times :-) then the command
``apt-mirror-updater --update-package-lists`` should work around this annoying
failure mode (by automatically switching to a different mirror when 'hash sum
mismatch' errors are encountered).

Publishing `apt-mirror-updater` to the world is my attempt to contribute to
this situation instead of complaining in bug trackers (see above) where no
robust and automated solution is emerging (at the time of writing). Who knows,
maybe some day these issues will be resolved by moving logic similar to what
I've implemented here into ``apt-get`` itself. Of course it would also help if
mirror updates were atomic...

Contact
-------

The latest version of `apt-mirror-updater` is available on PyPI_ and GitHub_.
The documentation is hosted on `Read the Docs`_. For bug reports please create
an issue on GitHub_. If you have questions, suggestions, etc. feel free to send
me an e-mail at `peter@peterodding.com`_.

License
-------

This software is licensed under the `MIT license`_.

© 2017 Peter Odding.


.. External references:
.. _apt-get: https://en.wikipedia.org/wiki/Advanced_Packaging_Tool
.. _at work: http://www.paylogic.com/
.. _Debian bug #110837: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=110837
.. _Debian bug #624122: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=624122
.. _Debian: https://en.wikipedia.org/wiki/Debian
.. _documentation: https://apt-mirror-updater.readthedocs.io
.. _GitHub: https://github.com/xolox/python-apt-mirror-updater
.. _MIT license: http://en.wikipedia.org/wiki/MIT_License
.. _per user site-packages directory: https://www.python.org/dev/peps/pep-0370/
.. _peter@peterodding.com: peter@peterodding.com
.. _PyPI: https://pypi.python.org/pypi/apt-mirror-updater
.. _Read the Docs: https://apt-mirror-updater.readthedocs.io
.. _Ubuntu: https://en.wikipedia.org/wiki/Ubuntu_(operating_system)
.. _virtual environments: http://docs.python-guide.org/en/latest/dev/virtualenvs/
