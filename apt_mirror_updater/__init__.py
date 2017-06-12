# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 12, 2017
# URL: https://apt-mirror-updater.readthedocs.io

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
import os
import sys
import time

# External dependencies.
from bs4 import UnicodeDammit
from capturer import CaptureOutput
from executor.contexts import ChangeRootContext, LocalContext
from humanfriendly import AutomaticSpinner, Timer, compact, format_timespan, pluralize
from property_manager import PropertyManager, cached_property, key_property, mutable_property, set_property
from six import text_type
from six.moves.urllib.parse import urlparse

# Modules included in our package.
from apt_mirror_updater.http import fetch_concurrent, fetch_url

# Semi-standard module versioning.
__version__ = '2.1'

MAIN_SOURCES_LIST = '/etc/apt/sources.list'
"""The absolute pathname of the list of configured APT data sources (a string)."""

SOURCES_LIST_ENCODING = 'UTF-8'
"""The text encoding of :data:`MAIN_SOURCES_LIST` (a string)."""

MAX_MIRRORS = 50
"""Limits the number of mirrors ranked by :func:`prioritize_mirrors()` (a number)."""

LAST_UPDATED_DEFAULT = 60 * 60 * 24 * 7 * 4
"""A default, pessimistic :attr:`~CandidateMirror.last_updated` value (a number)."""

UBUNTU_SECURITY_URL = 'http://security.ubuntu.com/ubuntu'
"""The URL where Ubuntu security updates are hosted (a string)."""

UBUNTU_OLD_RELEASES_URL = 'http://old-releases.ubuntu.com/ubuntu/'
"""The URL where EOL (end of life) Ubuntu suites are hosted (a string)."""

# Initialize a logger for this program.
logger = logging.getLogger(__name__)


class AptMirrorUpdater(PropertyManager):

    """Python API for the `apt-mirror-updater` package."""

    def __init__(self, **options):
        """
        Initialize an :class:`AptMirrorUpdater` object.

        :param options: Refer to the :class:`.PropertyManager` initializer for
                        details on argument handling.
        """
        # Initialize our superclass.
        super(AptMirrorUpdater, self).__init__(**options)
        # Initialize instance variables.
        self.blacklist = set()
        self.mirror_validity = dict()

    @mutable_property(cached=True)
    def context(self):
        """An execution context created using :mod:`executor.contexts` (defaults to :class:`.LocalContext`)."""
        return LocalContext()

    @mutable_property
    def distributor_id(self):
        """
        The distributor ID (a lowercase string like 'debian' or 'ubuntu').

        The value of this property defaults to the value of the
        :attr:`executor.contexts.AbstractContext.distributor_id`
        property which is the right choice 99% of the time.

        An example of a situation where it's not the right choice is when you
        want to create a chroot_ using debootstrap_: In this case the host
        system's :attr:`distributor_id` and :attr:`distribution_codename` may
        very well differ from those inside the chroot.

        .. _chroot: https://en.wikipedia.org/wiki/chroot
        .. _debootstrap: https://en.wikipedia.org/wiki/debootstrap
        """
        return self.context.distributor_id

    @mutable_property
    def distribution_codename(self):
        """
        The distribution codename (a lowercase string like 'trusty' or 'xenial').

        The value of this property defaults to the value of the
        :attr:`executor.contexts.AbstractContext.distribution_codename`
        property which is the right choice 99% of the time.
        """
        return self.context.distribution_codename

    @mutable_property
    def max_mirrors(self):
        """Limits the number of mirrors to rank (a number, defaults to :data:`MAX_MIRRORS`)."""
        return MAX_MIRRORS

    @cached_property
    def backend(self):
        """
        The backend module whose name matches :attr:`distributor_id`.

        :raises: :exc:`~exceptions.EnvironmentError` when no matching backend
                 module is available.
        """
        logger.debug("Checking whether %s platform is supported ..", self.distributor_id.capitalize())
        module_path = "%s.backends.%s" % (__name__, self.distributor_id)
        try:
            __import__(module_path)
        except ImportError:
            msg = "%s platform is unsupported! (only Debian and Ubuntu are supported)"
            raise EnvironmentError(msg % self.distributor_id.capitalize())
        else:
            return sys.modules[module_path]

    @cached_property
    def available_mirrors(self):
        """
        A set of :class:`CandidateMirror` objects with available mirrors for the current platform.

        Currently only Debian (see :mod:`apt_mirror_updater.backends.debian`)
        and Ubuntu (see :mod:`apt_mirror_updater.backends.ubuntu`) are
        supported. On other platforms :exc:`~exceptions.EnvironmentError` is
        raised.
        """
        mirrors = set()
        for candidate in self.backend.discover_mirrors():
            if any(fnmatch.fnmatch(candidate.mirror_url, pattern) for pattern in self.blacklist):
                logger.warning("Ignoring mirror %s because it matches the blacklist.", candidate.mirror_url)
            else:
                mirrors.add(candidate)
        # We make an attempt to incorporate the system's current mirror in
        # the candidates but we don't propagate failures while doing so.
        try:
            # Gotcha: We should never include the system's current mirror in
            # the candidates when we're bootstrapping a chroot for a different
            # platform.
            if self.distributor_id == self.context.distributor_id:
                mirrors.add(CandidateMirror(mirror_url=self.current_mirror))
        except Exception as e:
            logger.warning("Failed to add current mirror to set of available mirrors! (%s)", e)
        return mirrors

    @cached_property
    def prioritized_mirrors(self):
        """
        A list of :class:`CandidateMirror` objects based on :attr:`available_mirrors`.

        Refer to :func:`prioritize_mirrors()` for details about how this
        property's value is computed.
        """
        return prioritize_mirrors(self.available_mirrors, limit=self.max_mirrors)

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
        logger.debug("Selecting best %s mirror ..", self.distributor_id.capitalize())
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
        return find_current_mirror(self.get_sources_list())

    @cached_property
    def stable_mirror(self):
        """
        A mirror URL that is stable for the given execution context (a string).

        The value of this property defaults to the value of
        :attr:`current_mirror`, however if the current mirror can't be
        determined or is deemed inappropriate by :func:`validate_mirror()`
        then :attr:`best_mirror` will be used instead.

        This provides a stable mirror selection algorithm which is useful
        because switching mirrors causes ``apt-get update`` to unconditionally
        download all package lists and this takes a lot of time so should it be
        avoided when unnecessary.
        """
        try:
            logger.debug("Trying to select current mirror as stable mirror ..")
            if self.validate_mirror(self.current_mirror):
                return self.current_mirror
            else:
                logger.debug("Failed to validate current mirror, selecting best mirror instead ..")
        except Exception as e:
            logger.debug("Failed to determine current mirror, selecting best mirror instead (error was: %s) ..", e)
        return self.best_mirror

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

    def change_mirror(self, new_mirror=None, update=True):
        """
        Change the main mirror in use in :data:`MAIN_SOURCES_LIST`.

        :param new_mirror: The URL of the new mirror (a string, defaults to
                           :attr:`best_mirror`).
        :param update: Whether an ``apt-get update`` should be run after
                       changing the mirror (a boolean, defaults to
                       :data:`True`).
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
        sources_list = self.get_sources_list()
        current_mirror = find_current_mirror(sources_list)
        mirrors_to_replace = [current_mirror]
        if self.distributor_id == 'ubuntu':
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
        self.install_sources_list(u'\n'.join(lines))
        # Clear (relevant) cached properties.
        del self.current_mirror
        # Make sure previous package lists are removed.
        self.clear_package_lists()
        # Make sure the package lists are up to date.
        if update:
            self.smart_update(switch_mirrors=False)
        logger.info("Finished changing mirror of %s in %s.", self.context, timer)

    def get_sources_list(self):
        """
        Get the contents of :data:`MAIN_SOURCES_LIST`.

        :returns: A Unicode string.

        This code currently assumes that the ``sources.list`` file is encoded
        using :data:`SOURCES_LIST_ENCODING`. I'm not actually sure if this is
        correct because I haven't been able to find a formal specification!
        Feedback is welcome :-).
        """
        contents = self.context.read_file(MAIN_SOURCES_LIST)
        return contents.decode(SOURCES_LIST_ENCODING)

    def generate_sources_list(self, **options):
        """
        Generate the contents of ``/etc/apt/sources.list``.

        If no `mirror_url` keyword argument is given then :attr:`stable_mirror`
        is used as a default.

        Please refer to the documentation of the Debian
        (:func:`apt_mirror_updater.backends.debian.generate_sources_list()`)
        and Ubuntu (:func:`apt_mirror_updater.backends.ubuntu.generate_sources_list()`)
        backend implementations of this method for details on argument handling
        and the return value.
        """
        if options.get('mirror_url') is None:
            options['mirror_url'] = self.stable_mirror
        options.setdefault('codename', self.distribution_codename)
        return self.backend.generate_sources_list(**options)

    def install_sources_list(self, contents):
        """
        Install a new ``/etc/apt/sources.list`` file.

        :param contents: The new contents of the sources list (a Unicode
                         string). You can generate a suitable value using
                         the :func:`generate_sources_list()` method.
        """
        if isinstance(contents, text_type):
            contents = contents.encode(SOURCES_LIST_ENCODING)
        logger.info("Installing new %s ..", MAIN_SOURCES_LIST)
        with self.context:
            # Write the sources.list contents to a temporary file. We make sure
            # the file always ends in a newline to adhere to UNIX conventions.
            temporary_file = '/tmp/apt-mirror-updater-sources-list-%i.txt' % os.getpid()
            self.context.write_file(temporary_file, b'%s\n' % contents.rstrip())
            # Make sure the temporary file is cleaned up when we're done with it.
            self.context.cleanup('rm', '--force', temporary_file)
            # Make a backup copy of /etc/apt/sources.list in case shit hits the fan?
            if self.context.exists(MAIN_SOURCES_LIST):
                backup_copy = '%s.save.%i' % (MAIN_SOURCES_LIST, time.time())
                logger.info("Backing up contents of %s to %s ..", MAIN_SOURCES_LIST, backup_copy)
                self.context.execute('cp', MAIN_SOURCES_LIST, backup_copy, sudo=True)
            # Move the temporary file into place without changing ownership and permissions.
            self.context.execute(
                'cp', '--no-preserve=mode,ownership',
                temporary_file, MAIN_SOURCES_LIST,
                sudo=True,
            )

    def clear_package_lists(self):
        """Clear the package list cache by removing the files under ``/var/lib/apt/lists``."""
        timer = Timer()
        logger.info("Clearing package list cache of %s ..", self.context)
        self.context.execute(
            # We use an ugly but necessary find | xargs pipeline here because
            # find's -delete option implies -depth which negates -prune. Sigh.
            'find /var/lib/apt/lists -type f -name lock -prune -o -type f -print0 | xargs -0 rm -f',
            sudo=True,
        )
        logger.info("Successfully cleared package list cache of %s in %s.", self.context, timer)

    def dumb_update(self):
        """Update the system's package lists (by running ``apt-get update``)."""
        timer = Timer()
        logger.info("Updating package lists of %s ..", self.context)
        self.context.execute('apt-get', 'update', sudo=True)
        logger.info("Finished updating package lists of %s in %s ..", self.context, timer)

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
                        # Check for EOL suites. This somewhat peculiar way of
                        # checking is meant to ignore 404 responses from
                        # `secondary package mirrors' like PPAs.
                        maybe_end_of_life = any(
                            self.current_mirror in line and u'404' in line.split()
                            for line in output.splitlines()
                        )
                        # If the output of `apt-get update' implies that the
                        # suite is EOL we need to verify our assumption.
                        if maybe_end_of_life:
                            logger.warning("It looks like the current suite (%s) is EOL, verifying ..",
                                           self.distribution_codename)
                            if not self.validate_mirror(self.current_mirror):
                                if switch_mirrors:
                                    logger.warning("Switching to old releases mirror because current suite is EOL ..")
                                    self.change_mirror(UBUNTU_OLD_RELEASES_URL, update=False)
                                    continue
                                else:
                                    # When asked to do the impossible we abort
                                    # with a clear error message :-).
                                    raise Exception(compact("""
                                        Failed to update package lists because the
                                        current suite ({suite}) is end of life but
                                        I'm not allowed to switch mirrors! (there's
                                        no point in retrying so I'm not going to)
                                    """, suite=self.distribution_codename))
                        # Check for `hash sum mismatch' errors.
                        if switch_mirrors and u'hash sum mismatch' in output.lower():
                            logger.warning("Detected 'hash sum mismatch' failure, switching to other mirror ..")
                            self.ignore_mirror(self.current_mirror)
                            self.change_mirror(update=False)
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

    def create_chroot(self, directory, arch=None):
        """
        Bootstrap a basic Debian or Ubuntu system using debootstrap_.

        :param directory: The pathname of the target directory (a string).
        :param arch: The target architecture (a string or :data:`None`).
        :returns: A :class:`~executor.contexts.ChangeRootContext` object.

        If `directory` already exists and isn't empty then it is assumed that
        the chroot has already been created and debootstrap_ won't be run.
        Before this method returns it changes :attr:`context` to the chroot.
        """
        logger.debug("Checking if chroot already exists (%s) ..", directory)
        if self.context.exists(directory) and self.context.list_entries(directory):
            logger.debug("The chroot already exists, skipping initialization.")
            first_run = False
        else:
            # Ensure the `debootstrap' program is installed.
            if not self.context.find_program('debootstrap'):
                logger.info("Installing `debootstrap' program ..")
                self.context.execute('apt-get', 'install', '--yes', 'debootstrap', sudo=True)
            # Use the `debootstrap' program to create the chroot.
            timer = Timer()
            logger.info("Creating chroot using debootstrap (%s) ..", directory)
            debootstrap_command = ['debootstrap']
            if arch:
                debootstrap_command.append('--arch=%s' % arch)
            debootstrap_command.append(self.distribution_codename)
            debootstrap_command.append(directory)
            debootstrap_command.append(self.best_mirror)
            self.context.execute(*debootstrap_command, sudo=True)
            logger.info("Took %s to create chroot.", timer)
            first_run = True
        # Switch the execution context to the chroot and reset the locale (to
        # avoid locale warnings emitted by post-installation scripts run by
        # `apt-get install').
        self.context = ChangeRootContext(
            chroot=directory,
            environment=dict(LC_ALL='C'),
        )
        # Clear the values of cached properties that can be
        # invalidated by switching the execution context.
        del self.current_mirror
        del self.stable_mirror
        # The following initialization only needs to happen on the first
        # run, but it requires the context to be set to the chroot.
        if first_run:
            # Make sure the `lsb_release' program is available. It is
            # my experience that this package cannot be installed using
            # `debootstrap --include=lsb-release', it specifically
            # needs to be installed afterwards.
            self.context.execute('apt-get', 'install', '--yes', 'lsb-release', sudo=True)
            # Cleanup downloaded *.deb archives.
            self.context.execute('apt-get', 'clean', sudo=True)
            # Install a suitable /etc/apt/sources.list file. The logic behind
            # generate_sources_list() depends on the `lsb_release' program.
            self.install_sources_list(self.generate_sources_list())
            # Make sure the package lists are up to date.
            self.smart_update()
        return self.context


class CandidateMirror(PropertyManager):

    """A candidate mirror groups a mirror URL with its availability and performance metrics."""

    @key_property
    def mirror_url(self):
        """The base URL of the mirror (a string)."""

    @mutable_property
    def index_page(self):
        """The HTML of the mirror's index page (a string or :data:`None`)."""

    @mutable_property
    def index_latency(self):
        """The time it took to download the mirror's index page (a floating point number or :data:`None`)."""

    @mutable_property
    def last_updated(self):
        """The time in seconds since the last mirror update (a number or :data:`None`)."""

    @mutable_property
    def is_available(self):
        """
        :data:`True` if the index page of the mirror was successfully fetched, :data:`False` otherwise.

        The value of this property is computed by checking whether
        :attr:`index_page` is 'nonempty' (so not an empty string and
        also not :data:`None`). This is considered a sufficient check
        because :func:`apt_mirror_updater.http.fetch_url()` ensures
        that the status code of the HTTP response is 200.
        """
        return bool(self.index_page)

    @mutable_property
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
        is_updating = False
        if self.is_available:
            # I've seen UnicodeDammit fail to produce decoded output [2] and
            # although I don't fully understand that situation (I couldn't
            # reproduce it locally) I do think apt-mirror-updater shouldn't
            # fail in this situation. As such this code has been changed to
            # swallow exceptions instead of propagating them.
            #
            # [2] https://travis-ci.org/xolox/python-apt-mirror-updater/jobs/242128010
            try:
                components = urlparse(self.mirror_url)
                filename = u'Archive-Update-in-Progress-%s' % components.netloc
                dammit = UnicodeDammit(self.index_page)
                tokens = dammit.unicode_markup.split()
                is_updating = filename in tokens
            except Exception:
                pass
            # Use a (nasty) trick to conditionally cache the computed value.
            set_property(self, 'is_updating', is_updating)
        return is_updating

    @mutable_property
    def bandwidth(self):
        """The bytes per second achieved while fetching the mirror's index page (a number or :data:`None`)."""
        if self.index_page and self.index_latency:
            return len(self.index_page) / self.index_latency

    @mutable_property
    def sort_key(self):
        """
        A tuple that can be used to sort the mirror by its availability/performance metrics.

        The tuple created by this property contains four numbers in the following order:

        1. The number 1 when :attr:`is_available` is :data:`True` or
           the number 0 when :attr:`is_available` is :data:`False`
           (because most importantly a mirror must be available).
        2. The number 0 when :attr:`is_updating` is :data:`True` or
           the number 1 when :attr:`is_updating` is :data:`False`
           (because being updated at this very moment is *bad*).
        3. The negated value of :attr:`last_updated` (because the
           lower :attr:`last_updated` is, the better). If :attr:`last_updated`
           is :data:`None` then :data:`LAST_UPDATED_DEFAULT` is used instead.
        4. The value of :attr:`bandwidth` (because the higher
           :attr:`bandwidth` is, the better).

        By sorting :class:`CandidateMirror` objects on these tuples in
        ascending order, the last mirror in the sorted results will be the
        "most suitable mirror" (given the available information).
        """
        return (int(self.is_available),
                int(not self.is_updating),
                -(self.last_updated if self.last_updated is not None else LAST_UPDATED_DEFAULT),
                self.bandwidth or 0)


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
        # Gotcha: The following URL manipulation previously used urljoin()
        # and it would break when the mirror URL didn't end in a slash.
        fetch_url('%s/dists/%s' % (mirror_url.rstrip('/'), suite_name), retry=False)
        logger.info("Mirror %s is a valid choice for the suite %s.", mirror_url, suite_name)
        return True
    except Exception:
        logger.warning("It looks like the suite %s is EOL!", suite_name)
        return False


def prioritize_mirrors(mirrors, limit=MAX_MIRRORS, concurrency=None):
    """
    Rank the given mirrors by connection speed and update status.

    :param mirrors: An iterable of :class:`CandidateMirror` objects.
    :param limit: The maximum number of mirrors to query and report (a number,
                  defaults to :data:`MAX_MIRRORS`).
    :param concurrency: Refer to :func:`~apt_mirror_updater.http.fetch_concurrent()`.
    :returns: A list of :class:`CandidateMirror` objects where the first object
              is the highest ranking mirror (the best mirror) and the last
              object is the lowest ranking mirror (the worst mirror).
    :raises: If none of the given mirrors are available an exception is raised.
    """
    timer = Timer()
    # Sort the candidates based on the currently available information
    # (and transform the input argument into a list in the process).
    mirrors = sorted(mirrors, key=lambda c: c.sort_key, reverse=True)
    # Limit the number of candidates to a reasonable number?
    if limit and len(mirrors) > limit:
        mirrors = mirrors[:limit]
    mapping = dict((c.mirror_url, c) for c in mirrors)
    num_mirrors = pluralize(len(mapping), "mirror")
    logger.info("Checking %s for speed and update status ..", num_mirrors)
    with AutomaticSpinner(label="Checking mirrors"):
        for url, data, elapsed_time in fetch_concurrent(mapping.keys()):
            candidate = mapping[url]
            candidate.index_page = data
            candidate.index_latency = elapsed_time
    mirrors = list(mapping.values())
    logger.info("Finished checking speed and update status of %s (in %s).", num_mirrors, timer)
    if not any(c.is_available for c in mirrors):
        raise Exception("It looks like all %s are unavailable!" % num_mirrors)
    if all(c.is_updating for c in mirrors):
        logger.warning("It looks like all %s are being updated?!", num_mirrors)
    return sorted(mirrors, key=lambda c: c.sort_key, reverse=True)


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
