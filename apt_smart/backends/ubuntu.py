# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: martin68 and Peter Odding
# Last Change: September 15, 2019
# URL: https://apt-smart.readthedocs.io

"""Discovery of Ubuntu package archive mirrors."""

# Standard library modules.
import json
import logging

# External dependencies.
import six
from bs4 import BeautifulSoup, UnicodeDammit
from humanfriendly import Timer, format, pluralize

# Modules included in our package.
from apt_smart import CandidateMirror, mirrors_are_equal
from apt_smart.http import fetch_url

MIRRORS_URL = 'https://launchpad.net/ubuntu/+archivemirrors'
"""The URL of the HTML page listing official Ubuntu mirrors (a string)."""

MIRROR_SELECTION_URL = 'http://mirrors.ubuntu.com/mirrors.txt'
"""The URL of a plain text listing of "geographically suitable" mirror URLs (a string)."""

OLD_RELEASES_URL = 'http://old-releases.ubuntu.com/ubuntu/'
"""The URL where EOL (end of life) Ubuntu releases are hosted (a string)."""

SECURITY_URL = 'http://security.ubuntu.com/ubuntu'
"""The URL where Ubuntu security updates are hosted (a string)."""

BASE_URL = 'http://archive.ubuntu.com/ubuntu/dists/codename-security/InRelease'
"""The URL where official repo treated as base are hosted (a string).
The InRelease file contains `Date:` which can be gotten as :attr:`.base_last_updated`
to determine which mirrors are up-to-date"""

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


def discover_mirrors_old():
    """
    Discover available Ubuntu mirrors. (fallback)

    :returns: A set of :class:`.CandidateMirror` objects that have their
              :attr:`~.CandidateMirror.mirror_url` property set and may have
              the :attr:`~.CandidateMirror.last_updated` property set.
    :raises: If no mirrors are discovered an exception is raised.

    This queries :data:`MIRRORS_URL`to discover available Ubuntu mirrors.
    Here's an example run:

    >>> from apt_smart.backends.ubuntu import discover_mirrors_old
    >>> from pprint import pprint
    >>> pprint(discover_mirrors_old())
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

    It may be super-slow somewhere ( with 100Mbps fibre though ) in the world to access launchpad.net (see below),
    so we have to no longer rely on MIRRORS_URL .

    time curl -o/dev/null 'https://launchpad.net/ubuntu/+archivemirrors'
    % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
    100  263k  100  263k    0     0   5316      0  0:00:50  0:00:50 --:--:--  6398

    real    0m50.869s
    user    0m0.045s
    sys     0m0.039s

    But it can be a fallback when MIRROR_SELECTION_URL is down.
    """
    mirrors = set()
    logger.info("Discovering Ubuntu mirrors at %s ..", MIRRORS_URL)
    # Find which country the user is in to get mirrors in that country
    try:
        url = 'https://ipapi.co/json'
        response = fetch_url(url, timeout=2)
        # On py3 response is bytes and json.loads throws TypeError in py3.4 and 3.5,
        # so decode it to str
        if isinstance(response, six.binary_type):
            response = response.decode('utf-8')
        data = json.loads(response)
        country = data['country_name']
        logger.info("Found your location: %s by %s", country, url)
    except Exception:
        url = 'http://ip-api.com/json'
        response = fetch_url(url, timeout=5)
        if isinstance(response, six.binary_type):
            response = response.decode('utf-8')
        data = json.loads(response)
        country = data['country']
        logger.info("Found your location: %s by %s", country, url)

    data = fetch_url(MIRRORS_URL, timeout=70, retry=True)
    soup = BeautifulSoup(data, 'html.parser')
    tables = soup.findAll('table')
    flag = False  # flag is True when find the row's text is that country
    if not tables:
        raise Exception("Failed to locate <table> element in Ubuntu mirror page! (%s)" % MIRRORS_URL)
    else:
        for row in tables[0].findAll("tr"):
            if flag:
                if not row.a:  # End of mirrors located in that country
                    break
                else:
                    for a in row.findAll('a', href=True):
                        # Check if the link looks like a mirror URL.
                        if a['href'].startswith(('http://', 'https://')):
                            mirrors.add(CandidateMirror(mirror_url=a['href']))
            if row.th and row.th.get_text() == country:
                flag = True

    if not mirrors:
        raise Exception("Failed to discover any Ubuntu mirrors! (using %s)" % MIRRORS_URL)
    return mirrors


def discover_mirrors():
    """
    Discover available Ubuntu mirrors.

    :returns: A set of :class:`.CandidateMirror` objects that have their
              :attr:`~.CandidateMirror.mirror_url` property set and may have
              the :attr:`~.CandidateMirror.last_updated` property set.
    :raises: If no mirrors are discovered an exception is raised.

    This only queries :data:`MIRROR_SELECTION_URL` to
    discover available Ubuntu mirrors. Here's an example run:
    >>> from apt_smart.backends.ubuntu import discover_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_mirrors())

    """
    timer = Timer()
    mirrors = set()
    mirrors = discover_mirror_selection()
    if not mirrors:
        logger.warning("Failed to discover any Ubuntu mirrors! (using %s)" % MIRROR_SELECTION_URL)
        logger.info("Trying to use %s as fallback" % MIRRORS_URL)
        mirrors = discover_mirrors_old()
    elif len(mirrors) < 2:
        logger.warning("Too few mirrors, trying to use %s to find more" % MIRRORS_URL)
        mirrors |= discover_mirrors_old()  # add mirrors from discover_mirrors_old()
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Ubuntu mirror"), timer)
    return mirrors


def discover_mirror_selection():
    """Discover "geographically suitable" Ubuntu mirrors."""
    timer = Timer()
    logger.info("Identifying fast Ubuntu mirrors using %s ..", MIRROR_SELECTION_URL)
    data = fetch_url(MIRROR_SELECTION_URL, timeout=3, retry=True, max_attempts=5)
    # shorter timeout with more retries is good for unstable connections to MIRROR_SELECTION_URL
    dammit = UnicodeDammit(data)
    mirrors = set(
        CandidateMirror(mirror_url=mirror_url.strip())
        for mirror_url in dammit.unicode_markup.splitlines()
        if mirror_url and not mirror_url.isspace() and mirror_url.startswith(('http://', 'https://'))
    )
    logger.debug("Found %s in %s.", pluralize(len(mirrors), "fast Ubuntu mirror"), timer)
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
