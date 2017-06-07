# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 8, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Ubuntu package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import UnicodeDammit
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror
from apt_mirror_updater.http import fetch_url

UBUNTU_MIRRORS_URL = 'http://mirrors.ubuntu.com/mirrors.txt'
"""The URL of a text file that lists geographically close Ubuntu mirrors (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Ubuntu mirrors by querying :data:`UBUNTU_MIRRORS_URL`.

    :returns: A set of :class:`CandidateMirror` objects that have their
              :attr:`~CandidateMirror.mirror_url` property set.
    :raises: If no mirrors are discovered an exception is raised.

    An example run:

    >>> from apt_mirror_updater.backends.ubuntu import discover_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_mirrors())
    set([CandidateMirror(mirror_url='http://archive.ubuntu.com/ubuntu/'),
         CandidateMirror(mirror_url='http://ftp.nluug.nl/os/Linux/distr/ubuntu/'),
         CandidateMirror(mirror_url='http://ftp.snt.utwente.nl/pub/os/linux/ubuntu/'),
         CandidateMirror(mirror_url='http://ftp.tudelft.nl/archive.ubuntu.com/'),
         CandidateMirror(mirror_url='http://mirror.1000mbps.com/ubuntu/'),
         CandidateMirror(mirror_url='http://mirror.amsiohosting.net/archive.ubuntu.com/'),
         CandidateMirror(mirror_url='http://mirror.i3d.net/pub/ubuntu/'),
         CandidateMirror(mirror_url='http://mirror.nforce.com/pub/linux/ubuntu/'),
         CandidateMirror(mirror_url='http://mirror.nl.leaseweb.net/ubuntu/'),
         CandidateMirror(mirror_url='http://mirror.transip.net/ubuntu/ubuntu/'),
         ...])
    """
    timer = Timer()
    logger.info("Discovering available Ubuntu mirrors (using %s) ..", UBUNTU_MIRRORS_URL)
    response = fetch_url(UBUNTU_MIRRORS_URL, retry=True)
    dammit = UnicodeDammit(response.read())
    lines = dammit.unicode_markup.splitlines()
    mirrors = set(CandidateMirror(mirror_url=l.strip()) for l in lines if l and not l.isspace())
    if not mirrors:
        raise Exception("Failed to discover any Ubuntu mirrors! (using %s)" % UBUNTU_MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Ubuntu mirror"), timer)
    return mirrors
