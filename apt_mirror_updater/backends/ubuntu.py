# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 14, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Ubuntu package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, format, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror, mirrors_are_equal
from apt_mirror_updater.http import fetch_url

MIRRORS_URL = 'https://launchpad.net/ubuntu/+archivemirrors'
"""The URL of the HTML page listing official Ubuntu mirrors (a string)."""

OLD_RELEASES_URL = 'http://old-releases.ubuntu.com/ubuntu/'
"""The URL where EOL (end of life) Ubuntu releases are hosted (a string)."""

SECURITY_URL = 'http://security.ubuntu.com/ubuntu'
"""The URL where Ubuntu security updates are hosted (a string)."""

DEFAULT_SUITES = 'release', 'updates', 'backports', 'security'
"""A tuple of strings with the Ubuntu suites that are enabled by default."""

VALID_COMPONENTS = 'main', 'restricted', 'universe', 'multiverse'
"""A tuple of strings with the names of the components available in the Ubuntu package repositories."""

VALID_SUITES = 'release', 'security', 'updates', 'backports', 'proposed'
"""
A tuple of strings with the names of the suites available in the Ubuntu package
repositories.

The actual name of the 'release' suite is the codename of the relevant Ubuntu
release, while the names of the other suites are formed by concatenating the
codename with the suite name (separated by a dash).

As an example to make things more concrete, Ubuntu 16.04 has the following five
suites available: ``xenial`` (this is the release suite), ``xenial-security``,
``xenial-updates``, ``xenial-backports`` and ``xenial-proposed``.
"""

MIRROR_STATUSES = (
    ('Up to date', 0),
    ('One hour behind', 60 * 60),
    ('Two hours behind', 60 * 60 * 2),
    ('Four hours behind', 60 * 60 * 4),
    ('Six hours behind', 60 * 60 * 6),
    ('One day behind', 60 * 60 * 24),
    ('Two days behind', 60 * 60 * 24 * 2),
    ('One week behind', 60 * 60 * 24 * 7),
    ('Unknown', None),
)
r"""
A tuple of tuples with Launchpad mirror statuses. Each tuple consists of two values:

1. The human readable mirror latency (a string) as used on :data:`MIRRORS_URL`.
2. The mirror latency expressed in seconds (a number).

The 'known statuses' used by Launchpad were checked as follows:

.. code-block:: sh

   $ curl -s https://launchpad.net/+icing/rev18391/combo.css | tr '{},.' '\n' | grep distromirrorstatus
   distromirrorstatusUP
   distromirrorstatusONEHOURBEHIND
   distromirrorstatusTWOHOURSBEHIND
   distromirrorstatusFOURHOURSBEHIND
   distromirrorstatusSIXHOURSBEHIND
   distromirrorstatusONEDAYBEHIND
   distromirrorstatusTWODAYSBEHIND
   distromirrorstatusONEWEEKBEHIND
   distromirrorstatusUNKNOWN
"""

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Ubuntu mirrors by querying :data:`MIRRORS_URL`.

    :returns: A set of :class:`.CandidateMirror` objects that have their
              :attr:`~.CandidateMirror.mirror_url` property set and may have
              the :attr:`~.CandidateMirror.last_updated` property set.
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
    mirrors = set()
    logger.info("Discovering Ubuntu mirrors at %s ..", MIRRORS_URL)
    response = fetch_url(MIRRORS_URL, retry=True)
    soup = BeautifulSoup(response, 'html.parser')
    for table in soup.findAll('table'):
        for tr in table.findAll('tr'):
            for a in tr.findAll('a', href=True):
                # Check if the link looks like a mirror URL.
                if (a['href'].startswith(('http://', 'https://')) and
                        a['href'].endswith('/ubuntu/')):
                    # Try to figure out the mirror's reported latency.
                    last_updated = None
                    text = u''.join(tr.findAll(text=True))
                    for status_label, num_seconds in MIRROR_STATUSES:
                        if status_label in text:
                            last_updated = num_seconds
                            break
                    # Add the mirror to our overview.
                    mirrors.add(CandidateMirror(
                        mirror_url=a['href'],
                        last_updated=last_updated,
                    ))
                    # Skip to the next row.
                    break
    if not mirrors:
        raise Exception("Failed to discover any Ubuntu mirrors! (using %s)" % MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Ubuntu mirror"), timer)
    return mirrors


def generate_sources_list(mirror_url, codename,
                          suites=DEFAULT_SUITES,
                          components=VALID_COMPONENTS,
                          enable_sources=False):
    """
    Generate the contents of ``/etc/apt/sources.list`` for an Ubuntu system.

    :param mirror_url: The base URL of the mirror (a string).
    :param codename: The codename of the Ubuntu release (a string like 'trusty' or 'xenial').
    :param suites: An iterable of strings (defaults to :data:`DEFAULT_SUITES`,
                   refer to :data:`VALID_SUITES` for details).
    :param components: An iterable of strings (refer to
                       :data:`VALID_COMPONENTS` for details).
    :param enable_sources: :data:`True` to include ``deb-src`` entries,
                           :data:`False` to omit them.
    :returns: The suggested contents of ``/etc/apt/sources.list`` (a string).
    """
    # Validate the suites.
    invalid_suites = [s for s in suites if s not in VALID_SUITES]
    if invalid_suites:
        msg = "Invalid Ubuntu suite(s) given! (%s)"
        raise ValueError(msg % invalid_suites)
    # Validate the components.
    invalid_components = [c for c in components if c not in VALID_COMPONENTS]
    if invalid_components:
        msg = "Invalid Ubuntu component(s) given! (%s)"
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
                suite=(codename if suite == 'release' else codename + '-' + suite),
                components=' '.join(components),
            ))
    return '\n'.join(lines)
