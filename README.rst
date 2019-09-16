apt-smart: Smart, automated Debian/Ubuntu mirror selection
============================================================

.. image:: https://travis-ci.org/martin68/apt-smart.svg?branch=master
   :target: https://travis-ci.org/martin68/apt-smart

.. image:: https://coveralls.io/repos/martin68/apt-smart/badge.svg?branch=master
   :target: https://coveralls.io/r/martin68/apt-smart?branch=master

`简体中文 <https://github.com/martin68/apt-smart/blob/master/README-zh-cn.rst>`_

The `apt-smart` package automates robust apt-get_ mirror (a.k.a Repositories, Sources) selection for
Debian_ and Ubuntu_ by enabling smart discovery of available mirrors, smart ranking of
available mirrors, automatic switching between mirrors and robust package list
updating (see features_). It's currently tested on Python 2.7, 3.4, 3.5,
3.6, 3.7 and PyPy (although test coverage is still rather low, see status_).

.. contents::
   :local:

Why?
--------

As a successor of `apt-mirror-updater <https://github.com/xolox/python-apt-mirror-updater>`_,
`apt-smart` has many improvements in intelligence, speed, accuracy and robustness (see changelog_) when offering the best mirror for you.
It has a plan to optionally be a set-and-forget smart daemon: running in the background as a reverse proxy
always redirecting to the best mirror without root privilege. It also has a plan to support other distros like: Linux Mint , ROS...

.. _features:

Features
--------

**Smart discovery of available mirrors**
 Debian_ and Ubuntu_ mirrors are discovered automatically by querying the
 `Debian mirror list <https://www.debian.org/mirror/list>`_ or the `Ubuntu
 mirror list1 <http://mirrors.ubuntu.com/mirrors.txt>`_  or the `Ubuntu
 mirror list2 <https://launchpad.net/ubuntu/+archivemirrors>`_ (the applicable
 mirror list is automatically selected based on the current platform).
 It can smartly get mirrors within the country which the user is in.

**Smart ranking of available mirrors**
 Discovered mirrors are ranked by bandwidth (to pick the fastest mirror) and whether they're up-to-date and
 excluded if they're being updated (see `issues with mirror updates`_). e.g. with `--list-mirrors` flag it would output like this:

.. code-block:: sh

    -------------------------------------------------------------------------------------------------------------------------------------------------
    | Rank | Mirror URL                                       | Available? | Updating? | Last updated                               | Bandwidth     |
    -------------------------------------------------------------------------------------------------------------------------------------------------
    |    1 | http://archive.ubuntu.com/ubuntu                 | Yes        | No        | Up to date                                 | 16.95 KB/s    |
    |    2 | http://mirrors.cqu.edu.cn/ubuntu                 | Yes        | No        | 3 hours and 41 seconds behind              | 427.43 KB/s   |
    |    3 | http://mirrors.nju.edu.cn/ubuntu                 | Yes        | No        | 5 hours, 59 minutes and 5 seconds behind   | 643.27 KB/s   |
    |    4 | http://mirrors.tuna.tsinghua.edu.cn/ubuntu       | Yes        | No        | 5 hours, 59 minutes and 5 seconds behind   | 440.09 KB/s   |
    |    5 | http://mirrors.cn99.com/ubuntu                   | Yes        | No        | 13 hours, 36 minutes and 37 seconds behind | 2.64 MB/s     |
    |    6 | http://mirrors.huaweicloud.com/repository/ubuntu | Yes        | No        | 13 hours, 36 minutes and 37 seconds behind | 532.01 KB/s   |
    |    7 | http://mirrors.dgut.edu.cn/ubuntu                | Yes        | No        | 13 hours, 36 minutes and 37 seconds behind | 328.25 KB/s   |
    |    8 | http://mirrors.aliyun.com/ubuntu                 | Yes        | No        | 23 hours and 14 seconds behind             | 1.06 MB/s     |
    |    9 | http://ftp.sjtu.edu.cn/ubuntu                    | Yes        | No        | 23 hours and 14 seconds behind             | 647.2 KB/s    |
    |   10 | http://mirrors.yun-idc.com/ubuntu                | Yes        | No        | 23 hours and 14 seconds behind             | 526.6 KB/s    |
    |   11 | http://mirror.lzu.edu.cn/ubuntu                  | Yes        | No        | 23 hours and 14 seconds behind             | 210.99 KB/s   |
    |   12 | http://mirrors.ustc.edu.cn/ubuntu                | Yes        | Yes       | 8 hours, 59 minutes and 10 seconds behind  | 455.02 KB/s   |
    |   13 | http://mirrors.sohu.com/ubuntu                   | No         | No        | Unknown                                    | 90.28 bytes/s |
    -------------------------------------------------------------------------------------------------------------------------------------------------


**Automatic switching between mirrors**
 The main mirror configured in ``/etc/apt/sources.list`` can be changed with a
 single command. The new (to be configured) mirror can be selected
 automatically or configured explicitly by the user.

**Robust package list updating**
 Several apt-get_ subcommands can fail if the current mirror is being updated
 (see `issues with mirror updates`_) and `apt-smart` tries to work
 around this by wrapping ``apt-get update`` to retry on failures and
 automatically switch to a different mirror when it looks like the current
 mirror is being updated (because I've seen such updates take more than 15
 minutes and it's not always acceptable to wait for so long, especially in
 automated solutions).

.. _status:

Status
------

On the one hand the `apt-smart` package was developed based on quite a
few years of experience in using apt-get_ on Debian_ and Ubuntu_ systems. On the
other hand the Python package itself is relatively new: it was developed and
published in Sep 2019. As such:

.. warning:: Until `apt-smart` has been rigorously tested I consider
             it a proof of concept (beta software) so if it corrupts your
             system you can't complain that you weren't warned! The worst that can happen
             (assuming you trust my judgement ;-) is that
             ``/etc/apt/sources.list`` is corrupted however a backup copy is
             made before any changes are applied, so I don't see how this can
             result in irreversible corruption.

I'm working on an automated test suite but at the moment I'm still a bit fuzzy
on how to create representative tests for the error handling code paths (also,
writing a decent test suite requires a significant chunk of time :-).

Installation
------------

The `apt-smart` package is available on PyPI_ which means installation
should be as simple as:

.. code-block:: sh

   $ pip install --user apt-smart  # --user flag means install to per user site-packages directory(see below)
   $ echo "export PATH=\$(python -c 'import site; print(site.USER_BASE + \"/bin\")'):\$PATH" >> ~/.bashrc
   $ source ~/.bashrc  # set per user site-packages directory to PATH


There's actually a multitude of ways to install Python packages (e.g. the `per
user site-packages directory`_, `virtual environments`_ or just installing
system wide) and I have no intention of getting into that discussion here, so
if this intimidates you then read up on your options before returning to these
instructions ;-).

Usage
-----

There are two ways to use the `apt-smart` package: As the command line
program ``apt-smart`` and as a Python API. For details about the
Python API please refer to the API documentation available on `Read the Docs`_.
The command line interface is described below.

.. contents::
   :local:

.. A DRY solution to avoid duplication of the `apt-smart --help' text:
..
.. [[[cog
.. from humanfriendly.usage import inject_usage
.. inject_usage('apt_smart.cli')
.. ]]]

**Usage:** `apt-smart [OPTIONS]`

The apt-smart program automates robust apt-get mirror selection for
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
   "``-f``, ``--find-current-mirror``","Determine the main mirror that is currently configured in
   /etc/apt/sources.list and report its URL on standard output."
   "``-b``, ``--find-best-mirror``","Discover available mirrors, rank them, select the best one and report its
   URL on standard output."
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
   "``-v``, ``--verbose``",Increase logging verbosity (can be repeated).
   "``-V``, ``--version``",Show version number and Python version.
   "``-q``, ``--quiet``",Decrease logging verbosity (can be repeated).
   "``-h``, ``--help``",Show this message and exit.

.. [[[end]]]

.. _issues with mirror updates:

Issues with mirror updates
--------------------------

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
``apt-smart --update-package-lists`` should work around this annoying
failure mode (by automatically switching to a different mirror when 'hash sum
mismatch' errors are encountered).

Publishing `apt-smart` to the world is my attempt to contribute to
this situation instead of complaining in bug trackers (see above) where no
robust and automated solution is emerging (at the time of writing). Who knows,
maybe some day these issues will be resolved by moving logic similar to what
I've implemented here into ``apt-get`` itself. Of course it would also help if
mirror updates were atomic...

Contact
-------

The latest version of `apt-smart` is available on PyPI_ and GitHub_.
The documentation is hosted on `Read the Docs`_ and includes a changelog_. For
bug reports please create an issue on GitHub_.

License
-------

This software is licensed under the `MIT license`_.

© 2019 martin68

© 2018 Peter Odding.


.. External references:
.. _apt-get: https://en.wikipedia.org/wiki/Advanced_Packaging_Tool
.. _at work: http://www.paylogic.com/
.. _changelog: https://apt-smart.readthedocs.io/#change-log
.. _Debian bug #110837: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=110837
.. _Debian bug #624122: https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=624122
.. _Debian: https://en.wikipedia.org/wiki/Debian
.. _documentation: https://apt-smart.readthedocs.io
.. _GitHub: https://github.com/martin68/apt-smart
.. _MIT license: http://en.wikipedia.org/wiki/MIT_License
.. _per user site-packages directory: https://www.python.org/dev/peps/pep-0370/
.. _PyPI: https://pypi.python.org/pypi/apt-smart
.. _Read the Docs: https://apt-smart.readthedocs.io
.. _Ubuntu: https://en.wikipedia.org/wiki/Ubuntu_(operating_system)
.. _virtual environments: http://docs.python-guide.org/en/latest/dev/virtualenvs/
