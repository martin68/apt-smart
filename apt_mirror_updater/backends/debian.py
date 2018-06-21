# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 22, 2018
# URL: https://apt-mirror-updater.readthedocs.io

"""
Discovery of Debian package archive mirrors.

Here are references to some of the material that I've needed to consult while
working on this module:

- `Notes about sources.list on the Debian wiki <https://wiki.debian.org/SourcesList>`_
- `The Debian backports webpages <https://backports.debian.org/Instructions/>`_
- `Documentation about the "proposed-updates" mechanism <https://www.debian.org/releases/proposed-updates.html>`_
"""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, format, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror, mirrors_are_equal

from apt_mirror_updater.http import fetch_url

LTS_ARCHITECTURES = ('i386', 'amd64', 'armel', 'armhf')
"""The names of the architectures supported by the Debian LTS team (a tuple of strings)."""

LTS_RELEASES = {
    'jessie': 1593468000,  # 2020-06-30
    'stretch': 1656540000,  # 2022-06-30
}
"""
A dictionary with `Debian LTS`_ releases and their EOL dates.

This is needed because distro-info-data_ doesn't contain information
about Debian LTS releases but nevertheless ``archive.debian.org``
doesn't adopt a release until the LTS status expires (this was
originally reported in `issue #5`_).

.. _Debian LTS: https://wiki.debian.org/LTS
.. _issue #5: https://github.com/xolox/python-apt-mirror-updater/issues/5
"""

MIRRORS_URL = 'https://www.debian.org/mirror/list'
"""The URL of the HTML page listing all primary Debian mirrors (a string)."""

SECURITY_URL = 'http://security.debian.org/'
"""The base URL of the Debian mirror with security updates (a string)."""

OLD_RELEASES_URL = 'http://archive.debian.org/debian-archive/debian/'
"""The URL where EOL (end of life) Debian releases are hosted (a string)."""

DEFAULT_SUITES = 'release', 'security', 'updates'
"""A tuple of strings with the Debian suites that are enabled by default."""

VALID_COMPONENTS = 'main', 'contrib', 'non-free'
"""A tuple of strings with the names of the components available in the Debian package repositories."""

VALID_SUITES = 'release', 'security', 'updates', 'backports', 'proposed-updates'
"""A tuple of strings with the names of the suites available in the Debian package repositories."""

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Debian mirrors by querying :data:`MIRRORS_URL`.

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
    logger.info("Discovering Debian mirrors at %s ..", MIRRORS_URL)
    data = fetch_url(MIRRORS_URL, retry=True)
    soup = BeautifulSoup(data, 'html.parser')
    tables = soup.findAll('table')
    if not tables:
        raise Exception("Failed to locate <table> element in Debian mirror page! (%s)" % MIRRORS_URL)
    mirrors = set(CandidateMirror(mirror_url=a['href']) for a in tables[0].findAll('a', href=True))
    if not mirrors:
        raise Exception("Failed to discover any Debian mirrors! (using %s)" % MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Debian mirror"), timer)
    return mirrors


def generate_sources_list(mirror_url, codename,
                          suites=DEFAULT_SUITES,
                          components=VALID_COMPONENTS,
                          enable_sources=False):
    """
    Generate the contents of ``/etc/apt/sources.list`` for a Debian system.

    :param mirror_url: The base URL of the mirror (a string).
    :param codename: The codename of a Debian release (a string like 'wheezy'
                     or 'jessie') or a Debian release class (a string like
                     'stable', 'testing', etc).
    :param suites: An iterable of strings (defaults to
                   :data:`DEFAULT_SUITES`, refer to
                   :data:`VALID_SUITES` for details).
    :param components: An iterable of strings (refer to
                       :data:`VALID_COMPONENTS` for details).
    :param enable_sources: :data:`True` to include ``deb-src`` entries,
                           :data:`False` to omit them.
    :returns: The suggested contents of ``/etc/apt/sources.list`` (a string).
    """
    # Validate the suites.
    invalid_suites = [s for s in suites if s not in VALID_SUITES]
    if invalid_suites:
        msg = "Invalid Debian suite(s) given! (%s)"
        raise ValueError(msg % invalid_suites)
    # Validate the components.
    invalid_components = [c for c in components if c not in VALID_COMPONENTS]
    if invalid_components:
        msg = "Invalid Debian component(s) given! (%s)"
        raise ValueError(msg % invalid_components)
    # Generate the /etc/apt/sources.list file contents.
    lines = []
    directives = ('deb', 'deb-src') if enable_sources else ('deb',)
    for suite in suites:
        for directive in directives:
            lines.append(format(
                '{directive} {mirror} {suite} {components}', directive=directive,
                mirror=(OLD_RELEASES_URL if mirrors_are_equal(mirror_url, OLD_RELEASES_URL)
                        else (SECURITY_URL if suite == 'security' else mirror_url)),
                suite=(codename if suite == 'release' else (
                    ('%s/updates' % codename if suite == 'security'
                     else codename + '-' + suite))),
                components=' '.join(components),
            ))
    return '\n'.join(lines)


def get_eol_date(updater):
    """
    Override the EOL date for `Debian LTS`_ releases.

    :param updater: The :class:`~apt_mirror_updater.AptMirrorUpdater` object.
    :returns: The overridden EOL date (a number) or :data:`None`.
    """
    if updater.architecture in LTS_ARCHITECTURES:
        return LTS_RELEASES.get(updater.distribution_codename)
