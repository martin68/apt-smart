# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 8, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Discovery of Ubuntu package archive mirrors."""

# Standard library modules.
import logging

# External dependencies.
from bs4 import BeautifulSoup
from humanfriendly import Timer, pluralize

# Modules included in our package.
from apt_mirror_updater import CandidateMirror
from apt_mirror_updater.http import fetch_url

UBUNTU_MIRRORS_URL = 'https://launchpad.net/ubuntu/+archivemirrors'
"""The URL of the HTML page listing official Ubuntu mirrors (a string)."""

UBUNTU_MIRROR_STATUSES = (
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

1. The human readable mirror latency (a string) as used on :data:`UBUNTU_MIRRORS_URL`.
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

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


def discover_mirrors():
    """
    Discover available Ubuntu mirrors by querying :data:`UBUNTU_MIRRORS_URL`.

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
    logger.info("Discovering available Ubuntu mirrors (using %s) ..", UBUNTU_MIRRORS_URL)
    response = fetch_url(UBUNTU_MIRRORS_URL, retry=True)
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
                    for status_label, num_seconds in UBUNTU_MIRROR_STATUSES:
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
        raise Exception("Failed to discover any Ubuntu mirrors! (using %s)" % UBUNTU_MIRRORS_URL)
    logger.info("Discovered %s in %s.", pluralize(len(mirrors), "Ubuntu mirror"), timer)
    return mirrors
