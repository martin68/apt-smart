apt-smart: 智能的 Debian/Ubuntu 镜像源自动选择工具
============================================================

.. image:: https://travis-ci.org/martin68/apt-smart.svg?branch=master
   :target: https://travis-ci.org/martin68/apt-smart

.. image:: https://coveralls.io/repos/martin68/apt-smart/badge.svg?branch=master
   :target: https://coveralls.io/r/martin68/apt-smart?branch=master

`apt-smart` 提供健壮的 Debian_ 和 Ubuntu_  apt-get_ 镜像源 (也称软件源) 自动选择。
它能智能发现镜像源、排序镜像源并且自动切换，以及实现健壮的包列表更新 (参见 features_). 目前在 Python 2.7, 3.4, 3.5,
3.6, 3.7 和 PyPy 测试通过 (尽管test coverage目前还很低，参见 status_).

.. contents::
   :local:

Why?
--------

作为 `apt-mirror-updater <https://github.com/xolox/python-apt-mirror-updater>`_ 的继承者,
为你寻找最好镜像源的过程中 `apt-smart` 的智能、速度、准确性和健壮性方面都有提升和改进 (参见 changelog_)。
并且有计划增加反向代理模式——在设置好之后你就可以忘掉它，它在后台运行不需要root权限，在任何时候都指向最好的镜像源。
其他发行版如 Linux Mint， ROS等的支持也在计划之中。

.. _features:

Features
--------

**智能发现可用的镜像源**
 通过查询 `Debian mirror list <https://www.debian.org/mirror/list>`_ 或 `Ubuntu
 mirror list1 <http://mirrors.ubuntu.com/mirrors.txt>`_  或 `Ubuntu
 mirror list2 <https://launchpad.net/ubuntu/+archivemirrors>`_ (自动选择镜像源列表)来
 自动查找 Debian_ 和 Ubuntu_ 镜像源。它能够智能地获取用户所在国家的镜像源。

**智能排序可用的镜像源**
 可用镜像源按照如下方式排序：带宽、是否更新及时（up-to-date），并且排除了正在更新的镜像源 (参见 `issues with mirror updates`_)。
 例如使用 `--list-mirrors` 参数将会有类似输出：

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


**自动切换镜像源**
 设置在 ``/etc/apt/sources.list`` 的镜像源可以用一条很简单的命令更改。你可以让它自动选择镜像源或者由你指定。

**健壮的包列表更新**
 好几个 apt-get_ 的子命令在更新的过程中可能会失败 (参见 `issues with mirror updates`_) ，而`apt-smart` 通过
 wrap ``apt-get update`` 可以在检测到错误时重试，并且在检测到当前镜像源在“更新中“时自动切换至另一个镜像
 (因为曾经出现过“更新中“的状态持续很长时间，这有时是不可接受的，特别是在自动化维护的时候)

.. _status:

Status
------

尽管设置了自动测试，但项目还处于早期状态，所以:

.. warning:: `apt-smart` 弄坏你的系统可别怪我没警告过你哦! 但碰上最糟糕的情况恐怕也只有
             ``/etc/apt/sources.list`` 损坏了吧。程序在做任何更改前都会自动备份一次，
             所以应该能手动恢复。


Installation
------------

`apt-smart` 可以在 PyPI_ 上找到，所以安装应该很简单:

.. code-block:: sh

   $ pip install --user apt-smart # --user参数表示安装到per user site-packages directory
   $ echo "export PATH=\$(python -c 'import site; print(site.USER_BASE + \"/bin\")'):\$PATH" >> ~/.bashrc
   $ source ~/.bashrc  # 设置per user site-packages directory到PATH

安装 Python 包有几种方法 (例如 `per user site-packages directory`_, 或 `virtual environments`_ 或 安装到系统全局)
在这里不详细展开叙述。

Usage
-----

使用 `apt-smart` 有两种方法: 作为命令行工具 ``apt-smart`` 以及作为 Python API.
作为 Python API 的详细信息请参考文档—— `Read the Docs`_.
其命令行接口如下所示：

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

最常见的 ``apt-get update`` 错误是 'hash sum mismatch' (参见 `Debian bug #624122`_)。
当错误产生的时候，一个名为 ``Archive-Update-in-Progress-*`` 的文件有时会出现
该镜像源的首页 (参见 `Debian bug #110837`_). 这个状态有时会持续很长时间。

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
.. _changelog: https://apt-smart.readthedocs.io/changelog.html
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
