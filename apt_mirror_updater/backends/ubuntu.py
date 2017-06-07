# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 7, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Ubuntu package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import UnicodeDammit
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_mirror_updater.http import fetch_url

UBUNTU_MIRRORS_URL = 'http://mirrors.ubuntu.com/mirrors.txt'
"""The URL of a text file that lists geographically close Ubuntu mirrors (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Ubuntu mirrors by querying :data:`UBUNTU_MIRRORS_URL`.

    :returns: A set of strings with URLs of available mirrors.
    :raises: If no mirrors are discovered an exception is raised.

    An example run:

    >>> from apt_mirror_updater import discover_ubuntu_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_ubuntu_mirrors())
    set(['http://archive.ubuntu.com/ubuntu/',
         'http://ftp.nluug.nl/os/Linux/distr/ubuntu/',
         'http://ftp.snt.utwente.nl/pub/os/linux/ubuntu/',
         'http://ftp.tudelft.nl/archive.ubuntu.com/',
         'http://mirror.1000mbps.com/ubuntu/',
         'http://mirror.amsiohosting.net/archive.ubuntu.com/',
         'http://mirror.i3d.net/pub/ubuntu/',
         'http://mirror.nforce.com/pub/linux/ubuntu/',
         'http://mirror.nl.leaseweb.net/ubuntu/',
         'http://mirror.transip.net/ubuntu/ubuntu/',
         'http://mirrors.nl.eu.kernel.org/ubuntu/',
         'http://mirrors.noction.com/ubuntu/archive/',
         'http://nl.archive.ubuntu.com/ubuntu/',
         'http://nl3.archive.ubuntu.com/ubuntu/',
         'http://osmirror.rug.nl/ubuntu/',
         'http://ubuntu.mirror.cambrium.nl/ubuntu/'])
    """
    timer = Timer()
    logger.info("Discovering available Ubuntu mirrors (using %s) ..", UBUNTU_MIRRORS_URL)
    response = fetch_url(UBUNTU_MIRRORS_URL, retry=True)
    dammit = UnicodeDammit(response.read())
    lines = dammit.unicode_markup.splitlines()
    mirrors = set(l.strip() for l in lines if l and not l.isspace())
    if not mirrors:
        raise Exception("Failed to discover any Ubuntu mirrors! (using %s)" % UBUNTU_MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Ubuntu mirror"), timer)
    return mirrors
