# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 8, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Debian package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror
from apt_mirror_updater.http import fetch_url

DEBIAN_MIRRORS_URL = 'https://www.debian.org/mirror/list'
"""The URL of the HTML page listing all primary Debian mirrors (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Debian mirrors by querying :data:`DEBIAN_MIRRORS_URL`.

    :returns: A set of :class:`.CandidateMirror` objects that have their
             :attr:`~.CandidateMirror.mirror_url` property set.
    :raises: If no mirrors are discovered an exception is raised.

    An example run:

    >>> from apt_mirror_updater.backends.debian import discover_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_mirrors())
    set([CandidateMirror(mirror_url='http://ftp.at.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.au.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.be.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.bg.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.br.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.by.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.ca.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.ch.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.cn.debian.org/debian/'),
         CandidateMirror(mirror_url='http://ftp.cz.debian.org/debian/'),
         ...])
    """
    timer = Timer()
    logger.info("Discovering Debian mirrors (using %s) ..", DEBIAN_MIRRORS_URL)
    response = fetch_url(DEBIAN_MIRRORS_URL, retry=True)
    soup = BeautifulSoup(response, 'html.parser')
    tables = soup.findAll('table')
    if not tables:
        raise Exception("Failed to locate <table> element in Debian mirror page! (%s)" % DEBIAN_MIRRORS_URL)
    mirrors = set(CandidateMirror(mirror_url=a['href']) for a in tables[0].findAll('a', href=True))
    if not mirrors:
        raise Exception("Failed to discover any Debian mirrors! (using %s)" % DEBIAN_MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Debian mirror"), timer)
    return mirrors
