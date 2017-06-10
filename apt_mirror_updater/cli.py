# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 10, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""
Usage: apt-mirror-updater [OPTIONS]

The apt-mirror-updater program automates robust apt-get mirror selection for
Debian and Ubuntu by enabling discovery of available mirrors, ranking of
available mirrors, automatic switching between mirrors and robust package list
updating.

Supported options:

  -r, --remote-host=SSH_ALIAS

    Operate on a remote system instead of the local system. The SSH_ALIAS
    argument gives the SSH alias of the remote host. It is assumed that the
    remote account has root privileges or password-less sudo access.

  -f, --find-current-mirror

    Determine the URL of the main mirror that is currently configured in
    /etc/apt/sources.list.

  -l, --list-mirrors

    List available (ranked) mirrors on the terminal in a human readable format.

  -c, --change-mirror=MIRROR_URL

    Update /etc/apt/sources.list to use the given MIRROR_URL.

  -a, --auto-change-mirror

    Discover available mirrors, rank the mirrors by connection speed and update
    status and update /etc/apt/sources.list to use the best available mirror.

  -u, --update, --update-package-lists

    Update the package lists using `apt-get update', retrying on failure and
    automatically switch to a different mirror when it looks like the current
    mirror is being updated.

  -x, --exclude=PATTERN

    Add a pattern to the mirror selection blacklist. PATTERN is expected to be
    a shell pattern (containing wild cards like `?' and `*') that is matched
    against the full URL of each mirror.

  -m, --max=COUNT

    Don't query more than COUNT mirrors for their connection status
    (defaults to 50). If you give the number 0 no limit will be applied.

    Because Ubuntu mirror discovery can report more than 300 mirrors it's
    useful to limit the number of mirrors that are queried, otherwise the
    ranking of mirrors will take a long time (because over 300 connections
    need to be established).

  -v, --verbose

    Increase logging verbosity (can be repeated).

  -q, --quiet

    Decrease logging verbosity (can be repeated).

  -h, --help

    Show this message and exit.
"""

# Standard library modules.
import functools
import getopt
import logging
import sys

# External dependencies.
import coloredlogs
from executor.contexts import LocalContext, RemoteContext
from humanfriendly import format_size, format_table, format_timespan
from humanfriendly.terminal import connected_to_terminal, output, usage, warning

# Modules included in our package.
from apt_mirror_updater import MAX_MIRRORS, AptMirrorUpdater

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def main():
    """Command line interface for the ``apt-mirror-updater`` program."""
    # Initialize logging to the terminal and system log.
    coloredlogs.install(syslog=True)
    # Command line option defaults.
    context = LocalContext()
    updater = AptMirrorUpdater(context=context)
    limit = MAX_MIRRORS
    actions = []
    # Parse the command line arguments.
    try:
        options, arguments = getopt.getopt(sys.argv[1:], 'r:flc:aux:m:vqh', [
            'remote-host=', 'find-current-mirror', 'list-mirrors',
            'change-mirror', 'auto-change-mirror', 'update',
            'update-package-lists', 'exclude=', 'max=', 'verbose', 'quiet',
            'help',
        ])
        for option, value in options:
            if option in ('-r', '--remote-host'):
                if actions:
                    msg = "The %s option should be the first option given on the command line!"
                    raise Exception(msg % option)
                context = RemoteContext(value)
                updater = AptMirrorUpdater(context=context)
            elif option in ('-f', '--find-current-mirror'):
                actions.append(functools.partial(report_current_mirror, updater))
            elif option in ('-l', '--list-mirrors'):
                actions.append(functools.partial(report_available_mirrors, updater))
            elif option in ('-c', '--change-mirror'):
                actions.append(functools.partial(updater.change_mirror, value))
            elif option in ('-a', '--auto-change-mirror'):
                actions.append(updater.change_mirror)
            elif option in ('-u', '--update', '--update-package-lists'):
                actions.append(updater.smart_update)
            elif option in ('-x', '--exclude'):
                actions.insert(0, functools.partial(updater.ignore_mirror, value))
            elif option in ('-m', '--max'):
                limit = int(value)
            elif option in ('-v', '--verbose'):
                coloredlogs.increase_verbosity()
            elif option in ('-q', '--quiet'):
                coloredlogs.decrease_verbosity()
            elif option in ('-h', '--help'):
                usage(__doc__)
                return
            else:
                assert False, "Unhandled option!"
        if not actions:
            usage(__doc__)
            return
        # Propagate options to the Python API.
        updater.max_mirrors = limit
    except Exception as e:
        warning("Error: Failed to parse command line arguments! (%s)" % e)
        sys.exit(1)
    # Perform the requested action(s).
    try:
        for callback in actions:
            callback()
    except Exception:
        logger.exception("Encountered unexpected exception! Aborting ..")
        sys.exit(1)


def report_current_mirror(updater):
    """Print the URL of the currently configured ``apt-get`` mirror."""
    output(updater.current_mirror)


def report_available_mirrors(updater):
    """Print the available mirrors to the terminal (in a human friendly format)."""
    if connected_to_terminal():
        have_bandwidth = any(c.bandwidth for c in updater.prioritized_mirrors)
        have_last_updated = any(c.last_updated is not None for c in updater.prioritized_mirrors)
        column_names = ["Rank", "Mirror URL", "Available?", "Updating?"]
        if have_last_updated:
            column_names.append("Last updated")
        if have_bandwidth:
            column_names.append("Bandwidth")
        data = []
        for i, candidate in enumerate(updater.prioritized_mirrors, start=1):
            row = [i, candidate.mirror_url,
                   "Yes" if candidate.is_available else "No",
                   "Yes" if candidate.is_updating else "No"]
            if have_last_updated:
                row.append("Up to date" if candidate.last_updated == 0 else (
                    "%s behind" % format_timespan(candidate.last_updated)
                    if candidate.last_updated else "Unknown"
                ))
            if have_bandwidth:
                row.append("%s/s" % format_size(round(candidate.bandwidth, 2))
                           if candidate.bandwidth else "Unknown")
            data.append(row)
        output(format_table(data, column_names=column_names))
    else:
        output(u"\n".join(
            candidate.mirror_url for candidate in updater.prioritized_mirrors
            if candidate.is_available and not candidate.is_updating
        ))
