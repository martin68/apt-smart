# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 10, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""
Discovery of Debian package archive mirrors.

Here are references to some of the material that I've needed to consult while
working on this module:

- `Notes about sources.list on the Debian wiki <https://wiki.debian.org/SourcesList>`_
- `The Debian backports webpages <https://backports.debian.org/Instructions/`_
- `Documentation about the "proposed-updates" mechanism <https://www.debian.org/releases/proposed-updates.html>`_
"""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, format, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror
from apt_mirror_updater.http import fetch_url

DEBIAN_MIRRORS_URL = 'https://www.debian.org/mirror/list'
"""The URL of the HTML page listing all primary Debian mirrors (a string)."""

DEBIAN_SECURITY_URL = 'http://security.debian.org/'
"""The base URL of the Debian mirror with security updates (a string)."""

VALID_DEBIAN_COMPONENTS = 'main', 'contrib', 'non-free'
"""A tuple of strings with the names of the components available in the Debian package repositories."""

VALID_DEBIAN_SUITES = 'release', 'security', 'updates', 'backports', 'proposed-updates'
"""A tuple of strings with the names of the suites available in the Debian package repositories."""

DEFAULT_DEBIAN_SUITES = 'release', 'security', 'updates'
"""A tuple of strings with the Debian suites that are enabled by default."""

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


def generate_sources_list(mirror_url, codename,
                          suites=DEFAULT_DEBIAN_SUITES,
                          components=VALID_DEBIAN_COMPONENTS,
                          enable_sources=False):
    """
    Generate the contents of ``/etc/apt/sources.list`` for a Debian system.

    :param mirror_url: The base URL of the mirror (a string).
    :param codename: The codename of a Debian release (a string like 'wheezy'
                     or 'jessie') or a Debian release class (a string like
                     'stable', 'testing', etc).
    :param suites: An iterable of strings (defaults to
                   :data:`DEFAULT_DEBIAN_SUITES`, refer to
                   :data:`VALID_DEBIAN_SUITES` for details).
    :param components: An iterable of strings (refer to
                       :data:`VALID_DEBIAN_COMPONENTS` for details).
    :param enable_sources: :data:`True` to include ``deb-src`` entries,
                           :data:`False` to omit them.
    :returns: The suggested contents of ``/etc/apt/sources.list`` (a string).
    """
    lines = []
    directives = ('deb', 'deb-src') if enable_sources else ('deb',)
    for suite in suites:
        for directive in directives:
            lines.append(format(
                '{directive} {mirror} {suite} {components}', directive=directive,
                mirror=(DEBIAN_SECURITY_URL if suite == 'security' else mirror_url),
                suite=(codename if suite == 'release' else (
                    ('%s/updates' % codename if suite == 'security'
                     else codename + '-' + suite))),
                components=' '.join(components),
            ))
    return '\n'.join(lines)
