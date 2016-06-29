# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 29, 2016
# URL: https://apt-mirror-updater.readthedocs.org

"""
Automated, robust ``apt-get`` mirror selection for Debian and Ubuntu.

The main entry point for this module is the :class:`AptMirrorUpdater` class, so
if you don't know where to start that would be a good place :-). You can also
take a look at the source code of the :mod:`apt_mirror_updater.cli` module for
an example that uses the :class:`AptMirrorUpdater` class.
"""

# Standard library modules.
import fnmatch
import logging
import multiprocessing
import os
import time

# Compatibility between Python 2 and 3.
try:
    # Python 2.
    from urllib2 import urlopen
    from urlparse import urljoin, urlparse
except ImportError:
    # Python 3.
    from urllib.request import urlopen
    from urllib.parse import urljoin, urlparse

# External dependencies.
from bs4 import BeautifulSoup, UnicodeDammit
from stopit import SignalTimeout
from capturer import CaptureOutput
from humanfriendly import Timer, format_size, format_timespan, pluralize
from property_manager import PropertyManager, cached_property, lazy_property, required_property

# Semi-standard module versioning.
__version__ = '0.2'

MAIN_SOURCES_LIST = '/etc/apt/sources.list'
"""The absolute pathname of the list of configured APT data sources (a string)."""

DEBIAN_MIRRORS_URL = 'https://www.debian.org/mirror/list'
"""The URL of the HTML page listing all primary Debian mirrors (a string)."""

UBUNTU_MIRRORS_URL = 'http://mirrors.ubuntu.com/mirrors.txt'
"""The URL of a text file that lists geographically close Ubuntu mirrors (a string)."""

UBUNTU_SECURITY_URL = 'http://security.ubuntu.com/ubuntu'
"""The URL where Ubuntu security updates are hosted (a string)."""

UBUNTU_OLD_RELEASES_URL = 'http://old-releases.ubuntu.com/ubuntu/'
"""The URL where EOL (end of life) Ubuntu suites are hosted (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)

# Stop the `stopit' logger from logging tracebacks.
logging.getLogger('stopit').setLevel(logging.ERROR)


class AptMirrorUpdater(PropertyManager):

    """Python API for the `apt-mirror-updater` package."""

    def __init__(self, context):
        """
        Initialize an :class:`AptMirrorUpdater` object.

        :param context: An execution context created using
                        :mod:`executor.contexts`.
        """
        self.context = context
        self.blacklist = set()
        self.mirror_validity = dict()

    @lazy_property
    def distributor_id(self):
        """
        The distributor ID of the system (a lowercased string like ``debian`` or ``ubuntu``).

        This is the output of ``lsb_release --short --id``.
        """
        return self.context.capture('lsb_release', '--short', '--id').lower()

    @lazy_property
    def distribution_codename(self):
        """
        The code name of the system's distribution (a lowercased string like ``precise`` or ``trusty``).

        This is the output of ``lsb_release --short --codename``.
        """
        return self.context.capture('lsb_release', '--short', '--codename').lower()

    @cached_property
    def available_mirrors(self):
        """
        A set of strings (URLs) with available mirrors for the current platform.

        Currently only Debian (see :func:`discover_debian_mirrors()`) and
        Ubuntu (see :func:`discover_ubuntu_mirrors()`) are supported. On other
        platforms :exc:`~exceptions.EnvironmentError` is raised.
        """
        logger.debug("Checking whether platform of %s is supported ..", self.context)
        available_handlers = dict(debian=discover_debian_mirrors, ubuntu=discover_ubuntu_mirrors)
        handler = available_handlers.get(self.distributor_id)
        if handler:
            mirrors = set()
            try:
                mirrors.add(self.current_mirror)
            except Exception as e:
                logger.warning("Failed to add current mirror to set of available mirrors! (%s)", e)
            for mirror_url in handler():
                if any(fnmatch.fnmatch(mirror_url, pattern) for pattern in self.blacklist):
                    logger.warning("Ignoring mirror %s because it matches the blacklist.", mirror_url)
                else:
                    mirrors.add(mirror_url)
            return mirrors
        else:
            msg = "Platform of %s (%s) is unsupported! (only Debian and Ubuntu are supported)"
            raise EnvironmentError(msg % (self.context, self.distributor_id))

    @cached_property
    def prioritized_mirrors(self):
        """
        A list of :class:`CandidateMirror` objects based on :attr:`available_mirrors`.

        Refer to :func:`prioritize_mirrors()` for details about how this
        property's value is computed.
        """
        return prioritize_mirrors(self.available_mirrors)

    @cached_property
    def best_mirror(self):
        """
        The URL of the mirror in :attr:`available_mirrors` that looks like the best choice.

        This is a shortcut for using :func:`prioritize_mirrors()` to select the
        best mirror from :attr:`available_mirrors` and validating the current
        suite's availability using :func:`validate_mirror()`. If
        :attr:`available_mirrors` is empty an exception is raised.

        :raises: If the current suite is EOL (end of life) but there's no fall
                 back mirror available an exception is raised.
        """
        logger.debug("Selecting best mirror for %s ..", self.context)
        mirror_url = self.prioritized_mirrors[0].mirror_url
        if self.validate_mirror(mirror_url):
            return mirror_url
        elif self.distributor_id == 'ubuntu':
            logger.info("Falling back to Ubuntu's old releases mirror (%s).", UBUNTU_OLD_RELEASES_URL)
            return UBUNTU_OLD_RELEASES_URL
        else:
            msg = "It looks like the suite %s is EOL (end of life) but there's no fall back available for %s!"
            raise Exception(msg % (self.distribution_codename, self.distributor_id))

    @cached_property
    def current_mirror(self):
        """
        The URL of the main mirror in use in :data:`MAIN_SOURCES_LIST` (a string).

        The :attr:`current_mirror` property's value is computed using
        :func:`find_current_mirror()`.
        """
        logger.debug("Parsing %s to find current mirror of %s ..", MAIN_SOURCES_LIST, self.context)
        return find_current_mirror(self.context.capture('cat', MAIN_SOURCES_LIST))

    def validate_mirror(self, mirror_url):
        """
        Make sure a mirror serves the given suite.

        :param mirror_url: The base URL of the mirror (a string).
        :returns: :data:`True` if the mirror hosts the relevant suite,
                  :data:`False` otherwise.

        The :func:`validate_mirror()` method is a trivial wrapper for
        :func:`check_suite_available()` that avoids validating a mirror more
        than once.
        """
        key = (mirror_url, self.distribution_codename)
        if key not in self.mirror_validity:
            self.mirror_validity[key] = check_suite_available(mirror_url, self.distribution_codename)
        return self.mirror_validity[key]

    def ignore_mirror(self, pattern):
        """
        Add a pattern to the mirror discovery blacklist.

        :param pattern: A shell pattern (containing wild cards like ``?`` and
                        ``*``) that is matched against the full URL of each
                        mirror.

        When a pattern is added to the blacklist any previously cached value of
        :attr:`available_mirrors` is cleared to make sure that mirrors
        blacklisted after mirror discovery has run are ignored as well.
        """
        # Update the blacklist.
        logger.info("Adding pattern to mirror discovery blacklist: %s", pattern)
        self.blacklist.add(pattern)
        # Clear (relevant) cached properties.
        del self.available_mirrors
        del self.best_mirror

    def change_mirror(self, new_mirror=None):
        """
        Change the main mirror in use in :data:`MAIN_SOURCES_LIST`.

        :param new_mirror: The URL of the new mirror (a string, defaults to
                           :attr:`best_mirror`).
        """
        timer = Timer()
        # Default to the best available mirror.
        if new_mirror:
            logger.info("Changing mirror of %s to %s ..", self.context, new_mirror)
        else:
            logger.info("Changing mirror of %s to best available mirror ..", self.context)
            new_mirror = self.best_mirror
            logger.info("Selected mirror: %s", new_mirror)
        # Parse /etc/apt/sources.list to replace the old mirror with the new one.
        sources_list = self.context.capture('cat', MAIN_SOURCES_LIST)
        current_mirror = find_current_mirror(sources_list)
        mirrors_to_replace = [current_mirror]
        if new_mirror == UBUNTU_OLD_RELEASES_URL or not self.validate_mirror(new_mirror):
            # When a suite goes EOL the Ubuntu security updates mirror
            # stops serving that suite as well, so we need to remove it.
            logger.debug("Replacing %s URLs as well ..", UBUNTU_SECURITY_URL)
            mirrors_to_replace.append(UBUNTU_SECURITY_URL)
        else:
            logger.debug("Not touching %s URLs.", UBUNTU_SECURITY_URL)
        lines = sources_list.splitlines()
        for i, line in enumerate(lines):
            # The first token should be `deb' or `deb-src', the second token is
            # the mirror's URL, the third token is the `distribution' and any
            # further tokens are `components'.
            tokens = line.split()
            if (len(tokens) >= 4 and
                    tokens[0] in ('deb', 'deb-src') and
                    tokens[1] in mirrors_to_replace):
                tokens[1] = new_mirror
                lines[i] = u' '.join(tokens)
        # Install the modified package resource list.
        sources_list = u''.join('%s\n' % l for l in lines)
        logger.info("Updating %s on %s ..", MAIN_SOURCES_LIST, self.context)
        with self.context:
            # Write the updated sources.list contents to a temporary file.
            temporary_file = '/tmp/apt-mirror-updater-sources-list-%i.txt' % os.getpid()
            self.context.execute('cat > %s' % temporary_file, input=sources_list)
            # Make sure the temporary file is cleaned up when we're done with it.
            self.context.cleanup('rm', '--force', temporary_file)
            # Make a backup copy of /etc/apt/sources.list in case shit hits the fan.
            backup_copy = '%s.save.%i' % (MAIN_SOURCES_LIST, time.time())
            logger.info("Backing up contents of %s to %s ..", MAIN_SOURCES_LIST, backup_copy)
            self.context.execute('cp', MAIN_SOURCES_LIST, backup_copy, sudo=True)
            # Move the temporary file into place without changing ownership and permissions.
            self.context.execute(
                'cp', '--no-preserve=mode,ownership',
                temporary_file, MAIN_SOURCES_LIST,
                sudo=True,
            )
        # Clear (relevant) cached properties.
        del self.current_mirror
        # Make sure previous package lists are removed.
        self.clear_package_lists()
        # Make sure the package lists are up to date.
        self.smart_update(switch_mirrors=False)
        logger.info("Finished changing mirror of %s in %s.", self.context, timer)

    def clear_package_lists(self):
        """Clear the package list cache by removing all files under ``/var/lib/apt/lists``."""
        timer = Timer()
        logger.info("Clearing package list cache on %s ..", self.context)
        self.context.execute('find', '/var/lib/apt/lists', '-type', 'f', '-delete', sudo=True)
        logger.info("Successfully cleared package list cache on %s in %s.", self.context, timer)

    def dumb_update(self):
        """Update the system's package lists (by running ``apt-get update``)."""
        timer = Timer()
        logger.info("Updating package lists on %s ..", self.context)
        self.context.execute('apt-get', 'update', sudo=True)
        logger.info("Finished updating package lists on %s in %s ..", self.context, timer)

    def smart_update(self, max_attempts=10, switch_mirrors=True):
        """
        Update the system's package lists (switching mirrors if necessary).

        :param max_attempts: The maximum number of attempts at successfully
                             updating the system's package lists (an integer,
                             defaults to 10).
        :param switch_mirrors: :data:`True` if we're allowed to switch mirrors
                               on 'hash sum mismatch' errors, :data:`False`
                               otherwise.
        :raises: If updating of the package lists fails 10 consecutive times
                 (`max_attempts`) an exception is raised.

        While :func:`dumb_update()` simply runs ``apt-get update`` the
        :func:`smart_update()` function works quite differently:

        - First the system's package lists are updated using
          :func:`dumb_update()`. If this is successful we're done.
        - If the update fails we check the command's output for the phrase
          'hash sum mismatch'. If we find this phrase we assume that the
          current mirror is faulty and switch to another one.
        - Failing ``apt-get update`` runs are retried up to `max_attempts`.
        """
        backoff_time = 10
        for i in range(1, max_attempts + 1):
            with CaptureOutput() as session:
                try:
                    self.dumb_update()
                    return
                except Exception:
                    if i < max_attempts:
                        output = session.get_text()
                        if switch_mirrors and u'hash sum mismatch' in output.lower():
                            logger.warning("Detected 'hash sum mismatch' failure, switching to other mirror ..")
                            self.ignore_mirror(self.current_mirror)
                            self.change_mirror()
                        else:
                            logger.warning("Retrying after `apt-get update' failed (%i/%i) ..", i, max_attempts)
                            # Deal with unidentified (but hopefully transient) failures by retrying but backing off
                            # to give the environment (network connection, mirror state, etc.) time to stabilize.
                            logger.info("Sleeping for %s before retrying update ..", format_timespan(backoff_time))
                            time.sleep(backoff_time)
                            if backoff_time <= 120:
                                backoff_time *= 2
                            else:
                                backoff_time += backoff_time / 3
        raise Exception("Failed to update package lists %i consecutive times?!" % max_attempts)


class CandidateMirror(PropertyManager):

    """
    A candidate mirror that exposes availability and performance metrics.

    Here's an example:

    >>> from apt_mirror_updater import CandidateMirror
    >>> CandidateMirror('http://ftp.snt.utwente.nl/pub/os/linux/ubuntu/')
    CandidateMirror(bandwidth=55254.51,
                    is_available=True,
                    is_updating=False,
                    mirror_url='http://ftp.snt.utwente.nl/pub/os/linux/ubuntu/',
                    priority=55254.51)
    """

    def __init__(self, mirror_url):
        """
        Initialize a :class:`CandidateMirror` object.

        :param mirror_url: The base URL of the mirror (a string).
        """
        # Initialize the superclass.
        super(CandidateMirror, self).__init__(mirror_url=mirror_url)
        # Initialize internal state.
        self.timer = Timer(resumable=True)
        # Try to download the mirror's index page.
        with self.timer:
            logger.debug("Checking mirror %s ..", self.mirror_url)
            try:
                response = fetch_url(self.mirror_url, retry=False)
            except Exception as e:
                logger.debug("Encountered error while checking mirror %s! (%s)", self.mirror_url, e)
                self.index_page = None
            else:
                self.index_page = response.read()
                logger.debug("Downloaded %s at %s per second.", self.mirror_url, format_size(self.bandwidth))

    @required_property
    def mirror_url(self):
        """The base URL of the mirror (a string)."""

    @lazy_property
    def is_available(self):
        """:data:`True` if an HTTP connection to the mirror was successfully established, :data:`False` otherwise."""
        return self.index_page is not None

    @lazy_property
    def is_updating(self):
        """:data:`True` if it looks like the mirror is being updated, :data:`False` otherwise."""
        # Determine the name of the file which signals that this mirror is in the
        # process of being updated. I'm not sure how [1] but mirrors can host such
        # files for domains other than their own, so it's essential that we append
        # the mirror's domain name to the filename (to avoid false positives).
        #
        # [1] I guess this happens when a mirror pulls files from an upstream
        #     mirror while that upstream mirror is itself in the process of being
        #     updated. This has all sorts of awkward implications about robustness
        #     that I don't want to think about :-(.
        if self.is_available:
            components = urlparse(self.mirror_url)
            filename = u'Archive-Update-in-Progress-%s' % components.netloc
            dammit = UnicodeDammit(self.index_page)
            tokens = dammit.unicode_markup.split()
            return filename in tokens
        else:
            return False

    @lazy_property
    def bandwidth(self):
        """The bytes per second achieved while fetching the mirror's index page (an integer)."""
        return round(len(self.index_page) / self.timer.elapsed_time if self.is_available else 0, 2)

    @lazy_property
    def priority(self):
        """
        A number that indicates the preference for this mirror (where higher is better).

        The :attr:`priority` value is based on the :attr:`bandwidth` value but
        penalized when :data:`is_available` is :data:`False` or
        :attr:`is_updating` is :data:`True`.
        """
        if self.is_available:
            return -1000 if self.is_updating else self.bandwidth
        else:
            return 0


def discover_ubuntu_mirrors():
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


def discover_debian_mirrors():
    """
    Discover available Debian mirrors by querying :data:`DEBIAN_MIRRORS_URL`.

    :returns: A set of strings with URLs of available mirrors.
    :raises: If no mirrors are discovered an exception is raised.

    An example run:

    >>> from apt_mirror_updater import discover_debian_mirrors
    >>> from pprint import pprint
    >>> pprint(discover_debian_mirrors())
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


def check_suite_available(mirror_url, suite_name):
    """
    Make sure a mirror serves the given suite.

    :param mirror_url: The base URL of the mirror (a string).
    :param suite_name: The name of the suite that the mirror is expected to host (a string).
    :returns: :data:`True` if the mirror hosts the relevant suite,
              :data:`False` otherwise.
    """
    logger.info("Validating mirror %s for suite %s ..", mirror_url, suite_name)
    try:
        fetch_url(urljoin(mirror_url, u'dists/%s' % suite_name), retry=False)
        logger.info("Mirror %s is a valid choice for the suite %s.", mirror_url, suite_name)
        return True
    except Exception:
        logger.warning("It looks like the suite %s is EOL!", suite_name)
        return False


def prioritize_mirrors(mirror_urls, concurrency=4):
    """
    Rank the given mirror URL(s) by connection speed and update status.

    :param mirror_urls: A list of strings with mirror URL(s).
    :param concurrency: The number of URLs to query concurrently (an integer,
                        defaults to four).
    :returns: A list of :class:`CandidateMirror` objects where the first object
              is the highest ranking mirror (the best mirror) and the last
              object is the lowest ranking mirror (the worst mirror).
    :raises: If none of the given mirrors are available an exception is raised.
    """
    timer = Timer()
    num_mirrors = pluralize(len(mirror_urls), "mirror")
    logger.info("Checking %s for speed and update status ..", num_mirrors)
    pool = multiprocessing.Pool(concurrency)
    try:
        candidates = pool.map(CandidateMirror, mirror_urls, chunksize=1)
        logger.info("Finished checking speed and update status of %s (in %s).", num_mirrors, timer)
        if not any(mirror.is_available for mirror in candidates):
            raise Exception("It looks like all %s are unavailable!" % num_mirrors)
        if all(mirror.is_updating for mirror in candidates):
            logger.warning("It looks like all %s are being updated?!", num_mirrors)
        return sorted(candidates, key=lambda mirror: mirror.priority, reverse=True)
    finally:
        pool.terminate()


def find_current_mirror(sources_list):
    """
    Find the URL of the main mirror that is currently in use by ``apt-get``.

    :param sources_list: The contents of apt's package resource list, e.g. the
                         contents of :data:`MAIN_SOURCES_LIST` (a string).
    :returns: The URL of the main mirror in use (a string).
    :raises: If the main mirror can't be determined
             :exc:`~exceptions.EnvironmentError` is raised.

    The main mirror is determined by looking for the first ``deb`` or
    ``deb-src`` directive in apt's package resource list whose URL uses the
    HTTP or FTP scheme and whose components contain ``main``.
    """
    for line in sources_list.splitlines():
        # The first token should be `deb' or `deb-src', the second token is
        # the mirror's URL, the third token is the `distribution' and any
        # further tokens are `components'.
        tokens = line.split()
        if (len(tokens) >= 4 and
                tokens[0] in ('deb', 'deb-src') and
                tokens[1].startswith(('http://', 'ftp://')) and
                'main' in tokens[3:]):
            return tokens[1]
    raise EnvironmentError("Failed to determine current mirror in apt's package resource list!")


def fetch_url(url, timeout=10, retry=False, max_attempts=3):
    """
    Fetch a URL, optionally retrying on failure.

    :param url: The URL to fetch (a string).
    :param timeout: The maximum time in seconds that's allowed to pass before
                    the request is aborted (a number, defaults to 10 seconds).
    :param retry: Whether retry on failure is enabled (defaults to
                  :data:`False`).
    :param max_attempts: The maximum number of attempts when retrying is
                         enabled (an integer, defaults to three).
    :returns: The response object.
    :raises: Any exception raised by Python's standard library in the last
             attempt (assuming all attempts raise an exception).
    """
    timer = Timer()
    logger.debug("Fetching %s ..", url)
    for i in range(1, max_attempts + 1):
        try:
            with SignalTimeout(timeout, swallow_exc=False):
                response = urlopen(url)
                if response.getcode() != 200:
                    raise Exception("Got HTTP %i response when fetching %s!" % (response.getcode(), url))
        except Exception as e:
            if retry and i < max_attempts:
                logger.warning("Failed to fetch %s, retrying (%i/%i, error was: %s)", url, i, max_attempts, e)
            else:
                raise
        else:
            logger.debug("Took %s to fetch %s.", timer, url)
            return response
