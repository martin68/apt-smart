# Automated, robust apt-get mirror selection for Debian ,Ubuntu and Linux Mint.
#
# Author: martin68 and Peter Odding
# Last Change: October 29, 2019
# URL: https://apt-smart.readthedocs.io

"""Discovery of Linux Mint package archive mirrors."""

# Standard library modules.
import json
import logging

# External dependencies.
import six
from bs4 import BeautifulSoup
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_smart import CandidateMirror
from apt_smart.http import fetch_url

MIRRORS_URL = 'https://linuxmint.com/mirrors.php'
"""The URL of the HTML page listing official Linux Mint mirrors (a string)."""

SECURITY_URL = 'http://security.ubuntu.com/ubuntu'
"""The URL where Ubuntu ( Linux Mint's codebase )security updates are hosted (a string)."""

BASE_URL = 'http://packages.linuxmint.com/dists/codename/Release'
"""The URL where official repo treated as base are hosted (a string).
The Release file contains `Date:` which can be gotten as :attr:`.base_last_updated`
to determine which mirrors are up-to-date"""

DEFAULT_SUITES = 'release', 'updates', 'backports', 'security'
"""A tuple of strings with the Linux Mint suites that are enabled by default."""

VALID_COMPONENTS = 'main', 'restricted', 'universe', 'multiverse'
"""A tuple of strings with the names of the components available in the Linux Mint package repositories."""

VALID_SUITES = 'release', 'security', 'updates', 'backports', 'proposed'
"""
A tuple of strings with the names of the suites available in the Linux Mint package
repositories.

The actual name of the 'release' suite is the codename of the relevant Linux Mint
release, while the names of the other suites are formed by concatenating the
codename with the suite name (separated by a dash).

As an example to make things more concrete, Ubuntu 16.04 has the following five
suites available: ``xenial`` (this is the release suite), ``xenial-security``,
``xenial-updates``, ``xenial-backports`` and ``xenial-proposed``.
"""

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Linux Mint mirrors.

    :returns: A set of :class:`.CandidateMirror` objects that have their
              :attr:`~.CandidateMirror.mirror_url` property set and may have
              the :attr:`~.CandidateMirror.last_updated` property set.
    :raises: If no mirrors are discovered an exception is raised.

    This queries :data:`MIRRORS_URL`to discover available Linux Mint mirrors.
    Here's an example run:

    >>> from apt_smart.backends.linuxmint import discover_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_mirrors())
    set([CandidateMirror(mirror_url='http://mirrors.cqu.edu.cn/linuxmint/'),
         CandidateMirror(mirror_url='http://mirrors.hust.edu.cn/linuxmint/'),
         CandidateMirror(mirror_url='http://mirrors.shu.edu.cn/linuxmint/'),
         CandidateMirror(mirror_url='https://mirrors.tuna.tsinghua.edu.cn/linuxmint/'),
         CandidateMirror(mirror_url='http://mirrors.ustc.edu.cn/linuxmint/'),
         CandidateMirror(mirror_url='http://mirrors.zju.edu.cn/linuxmint/'),
         ...])
    """
    timer = Timer()
    mirrors = set()
    logger.info("Discovering Linux Mint mirrors at %s ..", MIRRORS_URL)
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
    if country == 'United States':
        country = 'USA'
    try:
        data = fetch_url(MIRRORS_URL, timeout=15)
    except Exception:
        logger.warning("Time out, try again")
        data = fetch_url(MIRRORS_URL, timeout=70, retry=True)
    soup = BeautifulSoup(data, 'html.parser')
    tables = soup.findAll('table')
    if not tables:
        raise Exception("Failed to locate <table> element in Linux Mint mirror page! (%s)" % MIRRORS_URL)
    else:
        while len(mirrors) < 3:
            for row in tables[2].findAll("tr"):
                if country in row.get_text():
                    for data in row.findAll("td"):
                        data_text = data.get_text()
                        # Check if the link looks like a mirror URL.
                        if data_text.startswith(('http://', 'https://')):
                            mirrors.add(CandidateMirror(mirror_url=data_text))
            if country == 'Worldwide':
                break  # break while loop when already set to 'Worldwide'
            if len(mirrors) < 3:
                logging.info("Too few mirrors found in your country, get more Worldwide mirrors.")
                country = 'Worldwide'

    if not mirrors:
        raise Exception("Failed to discover any Linux Mint mirrors! (using %s)" % MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Linux Mint mirror"), timer)
    return mirrors
