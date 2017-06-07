# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 7, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Debian package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_mirror_updater.http import fetch_url

DEBIAN_MIRRORS_URL = 'https://www.debian.org/mirror/list'
"""The URL of the HTML page listing all primary Debian mirrors (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Debian mirrors by querying :data:`DEBIAN_MIRRORS_URL`.

    :returns: A set of strings with URLs of available mirrors.
    :raises: If no mirrors are discovered an exception is raised.

    An example run:

    >>> from apt_mirror_updater.backends.debian import discover_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_mirrors())
    set(['http://ftp.at.debian.org/debian/',
         'http://ftp.au.debian.org/debian/',
         'http://ftp.be.debian.org/debian/',
         'http://ftp.bg.debian.org/debian/',
         'http://ftp.br.debian.org/debian/',
         'http://ftp.by.debian.org/debian/',
         'http://ftp.ca.debian.org/debian/',
         'http://ftp.ch.debian.org/debian/',
         'http://ftp.cn.debian.org/debian/',
         'http://ftp.cz.debian.org/debian/',
         'http://ftp.de.debian.org/debian/',
         'http://ftp.dk.debian.org/debian/',
         'http://ftp.ee.debian.org/debian/',
         'http://ftp.es.debian.org/debian/',
         'http://ftp.fi.debian.org/debian/',
         'http://ftp.fr.debian.org/debian/',
         'http://ftp.gr.debian.org/debian/',
         'http://ftp.hk.debian.org/debian/',
         'http://ftp.hr.debian.org/debian/',
         'http://ftp.hu.debian.org/debian/',
         'http://ftp.ie.debian.org/debian/',
         'http://ftp.is.debian.org/debian/',
         'http://ftp.it.debian.org/debian/',
         'http://ftp.jp.debian.org/debian/',
         'http://ftp.kr.debian.org/debian/',
         'http://ftp.lt.debian.org/debian/',
         'http://ftp.md.debian.org/debian/',
         'http://ftp.mx.debian.org/debian/',
         'http://ftp.nc.debian.org/debian/',
         'http://ftp.nl.debian.org/debian/',
         'http://ftp.no.debian.org/debian/',
         'http://ftp.nz.debian.org/debian/',
         'http://ftp.pl.debian.org/debian/',
         'http://ftp.pt.debian.org/debian/',
         'http://ftp.ro.debian.org/debian/',
         'http://ftp.ru.debian.org/debian/',
         'http://ftp.se.debian.org/debian/',
         'http://ftp.si.debian.org/debian/',
         'http://ftp.sk.debian.org/debian/',
         'http://ftp.sv.debian.org/debian/',
         'http://ftp.th.debian.org/debian/',
         'http://ftp.tr.debian.org/debian/',
         'http://ftp.tw.debian.org/debian/',
         'http://ftp.ua.debian.org/debian/',
         'http://ftp.uk.debian.org/debian/',
         'http://ftp.us.debian.org/debian/',
         'http://ftp2.de.debian.org/debian/',
         'http://ftp2.fr.debian.org/debian/'])
    """
    timer = Timer()
    logger.info("Discovering Debian mirrors (using %s) ..", DEBIAN_MIRRORS_URL)
    response = fetch_url(DEBIAN_MIRRORS_URL, retry=True)
    soup = BeautifulSoup(response, 'html.parser')
    tables = soup.findAll('table')
    if not tables:
        raise Exception("Failed to locate <table> element in Debian mirror page! (%s)" % DEBIAN_MIRRORS_URL)
    mirrors = set(a['href'] for a in tables[0].findAll('a', href=True))
    if not mirrors:
        raise Exception("Failed to discover any Debian mirrors! (using %s)" % DEBIAN_MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Debian mirror"), timer)
    return mirrors
