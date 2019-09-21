# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: martin68 and Peter Odding
# Last Change: September 15, 2019
# URL: https://apt-smart.readthedocs.io

"""
Usage: apt-smart [OPTIONS]

The apt-smart program automates robust apt-get mirror selection for
Debian and Ubuntu by enabling discovery of available mirrors, ranking of
available mirrors, automatic switching between mirrors and robust package list
updating.

Supported options:

  -r, --remote-host=SSH_ALIAS

    Operate on a remote system instead of the local system. The SSH_ALIAS
    argument gives the SSH alias of the remote host. It is assumed that the
    remote account has root privileges or password-less sudo access.

  -f, --find-current-mirror

    Determine the main mirror that is currently configured in
    /etc/apt/sources.list and report its URL on standard output.

  -F, --file-to-read=local_file_absolute_path

    Read a local absolute path file containing custom mirror URLs (one URL per line)
    to add custom mirrors to rank.

  -b, --find-best-mirror

    Discover available mirrors, rank them, select the best one and report its
    URL on standard output.

  -l, --list-mirrors

    List available (ranked) mirrors on the terminal in a human readable format.

  -L, --url-char-len=int

    An integer to specify the length of chars in mirrors' URL to display when
    using --list-mirrors, default is 34

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

  -v, --verbose

    Increase logging verbosity (can be repeated).

  -V, --version

    Show version number and Python version.

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
from apt_smart import MAX_MIRRORS, URL_CHAR_LEN, AptMirrorUpdater
from apt_smart import __version__ as updater_version

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def main():
    """Command line interface for the ``apt-smart`` program."""
    # Initialize logging to the terminal and system log.
    coloredlogs.install(syslog=True)
    # Command line option defaults.
    context = LocalContext()
    updater = AptMirrorUpdater(context=context)
    limit = MAX_MIRRORS
    url_char_len = URL_CHAR_LEN
    actions = []
    # Parse the command line arguments.
    try:
        options, arguments = getopt.getopt(sys.argv[1:], 'r:fF:blL:c:aux:m:vVqh', [
            'remote-host=', 'find-current-mirror', 'find-best-mirror', 'file-to-read=',
            'list-mirrors', 'url-char-len=', 'change-mirror', 'auto-change-mirror', 'update',
            'update-package-lists', 'exclude=', 'max=', 'verbose', 'version',
            'quiet', 'help',
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
            elif option in ('-F', '--file-to-read='):
                updater.custom_mirror_file_path = value
            elif option in ('-b', '--find-best-mirror'):
                actions.append(functools.partial(report_best_mirror, updater))
            elif option in ('-l', '--list-mirrors'):
                actions.append(functools.partial(report_available_mirrors, updater))
            elif option in ('-L', '--url-char-len'):
                url_char_len = int(value)
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
            elif option in ('-V', '--version'):
                output("Version: %s on Python %i.%i", updater_version, sys.version_info[0], sys.version_info[1])
                return
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
        updater.url_char_len = url_char_len
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


def report_best_mirror(updater):
    """Print the URL of the "best" mirror."""
    output(updater.best_mirror)


def report_available_mirrors(updater):
    """Print the available mirrors to the terminal (in a human friendly format)."""
    if connected_to_terminal():
        have_bandwidth = any(c.bandwidth for c in updater.ranked_mirrors)
        have_last_updated = any(c.last_updated is not None for c in updater.ranked_mirrors)
        column_names = ["Rank", "Mirror URL", "Available?", "Updating?"]
        if have_last_updated:
            column_names.append("Last updated")
        if have_bandwidth:
            column_names.append("Bandwidth")
        data = []
        long_mirror_urls = {}
        for i, candidate in enumerate(updater.ranked_mirrors, start=1):
            if len(candidate.mirror_url) <= updater.url_char_len:
                stripped_mirror_url = candidate.mirror_url
            else:  # the mirror_url is too long, strip it
                stripped_mirror_url = candidate.mirror_url[:updater.url_char_len - 3]
                stripped_mirror_url = stripped_mirror_url + "..."
                long_mirror_urls[str(i)] = candidate.mirror_url  # store it, output as full afterwards
            row = [i, stripped_mirror_url,
                   "Yes" if candidate.is_available else "No",
                   "Yes" if candidate.is_updating else "No"]
            if have_last_updated:
                row.append("Up to date" if candidate.last_updated == 0 else (
                    "%s behind" % format_timespan(candidate.last_updated, max_units=1)
                    if candidate.last_updated else "Unknown"
                ))
            if have_bandwidth:
                row.append("%s/s" % format_size(round(candidate.bandwidth, 0))
                           if candidate.bandwidth else "Unknown")
            data.append(row)
        output(format_table(data, column_names=column_names))
        if long_mirror_urls:
            output(u"Full URLs which are too long to be shown in above table:")
            for key, value in long_mirror_urls.items():
                output(u"%s: %s", key, value)
    else:
        output(u"\n".join(
            candidate.mirror_url for candidate in updater.ranked_mirrors
            if candidate.is_available and not candidate.is_updating
        ))
