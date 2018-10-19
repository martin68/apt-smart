# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 19, 2018
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

# Python 2.x / 3.x compatibility.
try:
    from enum import Enum
except ImportError:
    from flufl.enum import Enum

# External dependencies.
from capturer import CaptureOutput
from executor.contexts import ChangeRootContext, LocalContext
from humanfriendly import AutomaticSpinner, Timer, compact, format_timespan, pluralize
from property_manager import (
    PropertyManager,
    cached_property,
    key_property,
    lazy_property,
    mutable_property,
    set_property,
)
from six import text_type
from six.moves.urllib.parse import urlparse

# Modules included in our package.
from apt_mirror_updater.http import NotFoundError, fetch_concurrent, fetch_url, get_default_concurrency
from apt_mirror_updater.releases import coerce_release

# Semi-standard module versioning.
__version__ = '6.1'

MAIN_SOURCES_LIST = '/etc/apt/sources.list'
"""The absolute pathname of the list of configured APT data sources (a string)."""

SOURCES_LIST_ENCODING = 'UTF-8'
"""The text encoding of :data:`MAIN_SOURCES_LIST` (a string)."""

MAX_MIRRORS = 50
"""A sane default value for :attr:`AptMirrorUpdater.max_mirrors`."""

LAST_UPDATED_DEFAULT = 60 * 60 * 24 * 7 * 4
"""A default, pessimistic :attr:`~CandidateMirror.last_updated` value (a number)."""

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


class AptMirrorUpdater(PropertyManager):

    """Python API for the `apt-mirror-updater` program."""

    repr_properties = (
        'architecture',
        'backend',
        'blacklist',
        'concurrency',
        'context',
        'distribution_codename',
        'distributor_id',
        'max_mirrors',
        'old_releases_url',
        'security_url',
    )
    """
    Override the list of properties included in :func:`repr()` output (a tuple of strings).

    The :class:`~property_manager.PropertyManager` superclass defines a
    :class:`~property_manager.PropertyManager.__repr__()` method that includes
    the values of computed properties in its output.

    In the case of `apt-mirror-updater` this behavior would trigger external
    command execution and (lots of) HTTP calls, sometimes with unintended side
    effects, namely `infinite recursion`_.

    By setting :attr:`repr_properties` to a list of "safe" properties this
    problematic behavior can be avoided.

    .. _infinite recursion: https://travis-ci.org/xolox/python-apt-mirror-updater/jobs/395421319
    """

    @mutable_property
    def architecture(self):
        """
        The name of the Debian package architecture (a string like 'i386' or 'amd64').

        The package architecture is used to detect whether `Debian LTS`_ status
        applies to the given system (the Debian LTS team supports a specific
        subset of package architectures).

        .. _Debian LTS: https://wiki.debian.org/LTS
        """
        value = self.context.capture('dpkg', '--print-architecture')
        set_property(self, 'architecture', value)
        return value

    @cached_property
    def available_mirrors(self):
        """
        A list of :class:`CandidateMirror` objects (ordered from best to worst).

        On Ubuntu the mirrors will be ordered by the time since they were most
        recently updated. On Debian this information isn't available and the
        ordering of the list should be considered arbitrary.
        """
        mirrors = set()
        if self.release_is_eol:
            logger.debug("Skipping mirror discovery because %s is EOL.", self.release)
        else:
            for candidate in self.backend.discover_mirrors():
                if any(fnmatch.fnmatch(candidate.mirror_url, pattern) for pattern in self.blacklist):
                    logger.warning("Ignoring blacklisted mirror %s.", candidate.mirror_url)
                else:
                    candidate.updater = self
                    mirrors.add(candidate)
        # We make an attempt to incorporate the system's current mirror in
        # the candidates but we don't propagate failures while doing so.
        try:
            # Gotcha: We should never include the system's current mirror in
            # the candidates when we're bootstrapping a chroot for a different
            # platform.
            if self.distributor_id == self.context.distributor_id:
                mirrors.add(CandidateMirror(mirror_url=self.current_mirror, updater=self))
        except Exception as e:
            logger.warning("Failed to add current mirror to set of available mirrors! (%s)", e)
        # Sort the mirrors based on the currently available information.
        return sorted(mirrors, key=lambda c: c.sort_key, reverse=True)

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
    def best_mirror(self):
        """
        The URL of the first mirror in :attr:`ranked_mirrors` (a string).

        This is a shortcut for using :attr:`ranked_mirrors` to select the
        best mirror from :attr:`available_mirrors`, falling back to the
        old releases URL when :attr:`release_is_eol` is :data:`True`.
        """
        logger.debug("Selecting best %s mirror ..", self.distributor_id.capitalize())
        if self.release_is_eol:
            logger.info("%s is EOL, using %s.", self.release, self.old_releases_url)
            return self.old_releases_url
        else:
            return self.ranked_mirrors[0].mirror_url

    @cached_property
    def blacklist(self):
        """
        A set of strings with :mod:`fnmatch` patterns (defaults to an empty set).

        When :attr:`available_mirrors` encounters a mirror whose URL matches
        one of the patterns in :attr:`blacklist` the mirror will be ignored.
        """
        return set()

    @mutable_property
    def concurrency(self):
        """
        The number of concurrent HTTP connections allowed while ranking mirrors (a number).

        The value of this property defaults to the value computed by
        :func:`.get_default_concurrency()`.
        """
        return get_default_concurrency()

    @mutable_property(cached=True)
    def context(self):
        """
        An execution context created using :mod:`executor.contexts`.

        The value of this property defaults to a
        :class:`~executor.contexts.LocalContext` object.
        """
        return LocalContext()

    @cached_property
    def current_mirror(self):
        """
        The URL of the main mirror in use in :data:`MAIN_SOURCES_LIST` (a string).

        The :attr:`current_mirror` property's value is computed using
        :func:`find_current_mirror()`.
        """
        logger.debug("Parsing %s to find current mirror of %s ..", MAIN_SOURCES_LIST, self.context)
        return find_current_mirror(self.get_sources_list())

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
    def distributor_id(self):
        """
        The distributor ID (a lowercase string like 'debian' or 'ubuntu').

        The default value of this property is based on the
        :attr:`~apt_mirror_updater.releases.Release.distributor_id` property of
        :attr:`release` (which in turn is based on :attr:`distribution_codename`).

        Because Debian and Ubuntu code names are unambiguous this means that in
        practice you can provide a value for :attr:`distribution_codename` and
        omit :attr:`distributor_id` and everything should be fine.
        """
        return self.release.distributor_id

    @mutable_property
    def max_mirrors(self):
        """Limits the number of mirrors to rank (a number, defaults to :data:`MAX_MIRRORS`)."""
        return MAX_MIRRORS

    @mutable_property
    def old_releases_url(self):
        """The URL of the mirror that serves old releases for this :attr:`backend` (a string)."""
        return self.backend.OLD_RELEASES_URL

    @cached_property
    def ranked_mirrors(self):
        """
        A list of :class:`CandidateMirror` objects (ordered from best to worst).

        The value of this property is computed by concurrently testing the
        mirrors in :attr:`available_mirrors` for the following details:

        - availability (:attr:`~CandidateMirror.is_available`)
        - connection speed (:attr:`~CandidateMirror.bandwidth`)
        - update status (:attr:`~CandidateMirror.is_updating`)

        The number of mirrors to test is limited to :attr:`max_mirrors` and you
        can change the number of simultaneous HTTP connections allowed by
        setting :attr:`concurrency`.
        """
        timer = Timer()
        # Sort the candidates based on the currently available information
        # (and transform the input argument into a list in the process).
        mirrors = sorted(self.available_mirrors, key=lambda c: c.sort_key, reverse=True)
        # Limit the number of candidates to a reasonable number?
        if self.max_mirrors and len(mirrors) > self.max_mirrors:
            mirrors = mirrors[:self.max_mirrors]
        # Prepare the Release.gpg URLs to fetch.
        mapping = dict((c.release_gpg_url, c) for c in mirrors)
        num_mirrors = pluralize(len(mapping), "mirror")
        logger.info("Checking %s for availability and performance ..", num_mirrors)
        # Concurrently fetch the Release.gpg files.
        with AutomaticSpinner(label="Checking mirrors"):
            for url, data, elapsed_time in fetch_concurrent(mapping.keys(), concurrency=self.concurrency):
                candidate = mapping[url]
                candidate.release_gpg_contents = data
                candidate.release_gpg_latency = elapsed_time
        # Concurrently check for Archive-Update-in-Progress markers.
        update_mapping = dict((c.archive_update_in_progress_url, c) for c in mirrors if c.is_available)
        logger.info("Checking %s for Archive-Update-in-Progress marker ..",
                    pluralize(len(update_mapping), "mirror"))
        with AutomaticSpinner(label="Checking mirrors"):
            for url, data, elapsed_time in fetch_concurrent(update_mapping.keys(), concurrency=self.concurrency):
                update_mapping[url].is_updating = data is not None
        # Sanity check our results.
        mirrors = list(mapping.values())
        logger.info("Finished checking %s (took %s).", num_mirrors, timer)
        if not any(c.is_available for c in mirrors):
            raise Exception("It looks like all %s are unavailable!" % num_mirrors)
        if all(c.is_updating for c in mirrors):
            logger.warning("It looks like all %s are being updated?!", num_mirrors)
        return sorted(mirrors, key=lambda c: c.sort_key, reverse=True)

    @cached_property
    def release(self):
        """A :class:`.Release` object corresponding to :attr:`distributor_id` and :attr:`distribution_codename`."""
        return coerce_release(self.distribution_codename)

    @cached_property
    def release_is_eol(self):
        """
        :data:`True` if the release is EOL (end of life), :data:`False` otherwise.

        There are three ways in which the value of this property can be computed:

        - When available, the first of the following EOL dates will be compared
          against the current date to determine whether the release is EOL:

          - If the :attr:`backend` module contains a ``get_eol_date()``
            function (only the :mod:`~apt_mirror_updater.backends.debian`
            module does at the time of writing) then it is called and if it
            returns a number, this number is the EOL date for the release.

            This function was added to enable apt-mirror-updater backend
            modules to override the default EOL dates, more specifically to
            respect the `Debian LTS`_ release schedule (see also `issue #5`_).

          - Otherwise the :attr:`~apt_mirror_updater.releases.Release.eol_date`
            of :attr:`release` is used.

        - As a fall back :func:`validate_mirror()` is used to check whether
          :attr:`security_url` results in :data:`MirrorStatus.MAYBE_EOL`.

        .. _Debian LTS: https://wiki.debian.org/LTS
        .. _issue #5: https://github.com/xolox/python-apt-mirror-updater/issues/5
        """
        release_is_eol = None
        logger.debug("Checking whether %s is EOL ..", self.release)
        # Check if the backend provides custom EOL dates.
        if hasattr(self.backend, 'get_eol_date'):
            eol_date = self.backend.get_eol_date(self)
            if eol_date:
                release_is_eol = (time.time() >= eol_date)
                source = "custom EOL dates"
        # Check if the bundled data contains an applicable EOL date.
        if release_is_eol is None and self.release.eol_date:
            release_is_eol = self.release.is_eol
            source = "known EOL dates"
        # Validate the security mirror as a fall back.
        if release_is_eol is None:
            release_is_eol = (self.validate_mirror(self.security_url) == MirrorStatus.MAYBE_EOL)
            source = "security mirror"
        logger.debug(
            "%s is %s (based on %s).", self.release,
            "EOL" if release_is_eol else "supported", source,
        )
        return release_is_eol

    @mutable_property
    def security_url(self):
        """The URL of the mirror that serves security updates for this :attr:`backend` (a string)."""
        return self.backend.SECURITY_URL

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
        if self.release_is_eol:
            logger.debug("%s is EOL, falling back to %s.", self.release, self.old_releases_url)
            return self.old_releases_url
        else:
            try:
                logger.debug("Trying to select current mirror as stable mirror ..")
                return self.current_mirror
            except Exception:
                logger.debug("Failed to determine current mirror, selecting best mirror instead ..")
                return self.best_mirror

    @cached_property
    def validated_mirrors(self):
        """Dictionary of validated mirrors (used by :func:`validate_mirror()`)."""
        return {}

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
        mirrors_to_replace = [normalize_mirror_url(find_current_mirror(sources_list))]
        if self.release_is_eol:
            # When a release goes EOL the security updates mirrors stop
            # serving that release as well, so we need to remove them.
            logger.debug("Replacing %s URLs as well ..", self.security_url)
            mirrors_to_replace.append(normalize_mirror_url(self.security_url))
        else:
            logger.debug("Not replacing %s URLs.", self.security_url)
        lines = sources_list.splitlines()
        for i, line in enumerate(lines):
            # The first token should be `deb' or `deb-src', the second token is
            # the mirror's URL, the third token is the `distribution' and any
            # further tokens are `components'.
            tokens = line.split()
            if (len(tokens) >= 4 and
                    tokens[0] in ('deb', 'deb-src') and
                    normalize_mirror_url(tokens[1]) in mirrors_to_replace):
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

    def create_chroot(self, directory, arch=None):
        """
        Bootstrap a basic Debian or Ubuntu system using debootstrap_.

        :param directory: The pathname of the target directory (a string).
        :param arch: The target architecture (a string or :data:`None`).
        :returns: A :class:`~executor.contexts.ChangeRootContext` object.

        If `directory` already exists and isn't empty then it is assumed that
        the chroot has already been created and debootstrap_ won't be run.
        Before this method returns it changes :attr:`context` to the chroot.

        .. _debootstrap: https://manpages.debian.org/debootstrap
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
            logger.info("Creating %s chroot in %s ..", self.release, directory)
            debootstrap_command = ['debootstrap']
            if arch:
                debootstrap_command.append('--arch=%s' % arch)
            debootstrap_command.append('--keyring=%s' % self.release.keyring_file)
            debootstrap_command.append(self.distribution_codename)
            debootstrap_command.append(directory)
            debootstrap_command.append(self.best_mirror)
            self.context.execute(*debootstrap_command, sudo=True)
            logger.info("Took %s to create %s chroot.", timer, self.release)
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

    def dumb_update(self, *args):
        """
        Update the system's package lists (by running ``apt-get update``).

        :param args: Command line arguments to ``apt-get update`` (zero or more strings).

        The :func:`dumb_update()` method doesn't do any error handling or
        retrying, if that's what you're looking for then you need
        :func:`smart_update()` instead.
        """
        timer = Timer()
        logger.info("Updating package lists of %s ..", self.context)
        self.context.execute('apt-get', 'update', *args, sudo=True)
        logger.info("Finished updating package lists of %s in %s.", self.context, timer)

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

    def ignore_mirror(self, pattern):
        """
        Add a pattern to the mirror discovery :attr:`blacklist`.

        :param pattern: A shell pattern (containing wild cards like ``?`` and
                        ``*``) that is matched against the full URL of each
                        mirror.

        When a pattern is added to the blacklist any previously cached values
        of :attr:`available_mirrors`, :attr:`best_mirror`, :attr:`ranked_mirrors`
        and :attr:`stable_mirror` are cleared. This makes sure that mirrors
        blacklisted after mirror discovery has already run are ignored.
        """
        # Update the blacklist.
        logger.info("Adding pattern to mirror discovery blacklist: %s", pattern)
        self.blacklist.add(pattern)
        # Clear (relevant) cached properties.
        del self.available_mirrors
        del self.best_mirror
        del self.ranked_mirrors
        del self.stable_mirror

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

    def smart_update(self, *args, **kw):
        """
        Update the system's package lists (switching mirrors if necessary).

        :param args: Command line arguments to ``apt-get update`` (zero or more strings).
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
        max_attempts = kw.get('max_attempts', 10)
        switch_mirrors = kw.get('switch_mirrors', True)
        for i in range(1, max_attempts + 1):
            with CaptureOutput() as session:
                try:
                    self.dumb_update(*args)
                    return
                except Exception:
                    if i < max_attempts:
                        output = session.get_text()
                        # Check for EOL releases. This somewhat peculiar way of
                        # checking is meant to ignore 404 responses from
                        # `secondary package mirrors' like PPAs. If the output
                        # of `apt-get update' implies that the release is EOL
                        # we need to verify our assumption.
                        if any(self.current_mirror in line and u'404' in line.split() for line in output.splitlines()):
                            logger.warning("%s may be EOL, checking ..", self.release)
                            if self.release_is_eol:
                                if switch_mirrors:
                                    logger.warning("Switching to old releases mirror because %s is EOL ..",
                                                   self.release)
                                    self.change_mirror(self.old_releases_url, update=False)
                                    continue
                                else:
                                    raise Exception(compact("""
                                        Failed to update package lists because it looks like
                                        the current release (%s) is end of life but I'm not
                                        allowed to switch mirrors! (there's no point in
                                        retrying so I'm not going to)
                                    """, self.distribution_codename))
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

    def validate_mirror(self, mirror_url):
        """
        Make sure a mirror serves :attr:`distribution_codename`.

        :param mirror_url: The base URL of the mirror (a string).
        :returns: One of the values in the :class:`MirrorStatus` enumeration.

        This method assumes that :attr:`old_releases_url` is always valid.
        """
        if mirrors_are_equal(mirror_url, self.old_releases_url):
            return MirrorStatus.AVAILABLE
        else:
            mirror_url = normalize_mirror_url(mirror_url)
            key = (mirror_url, self.distribution_codename)
            value = self.validated_mirrors.get(key)
            if value is None:
                logger.info("Checking if %s is available on %s ..", self.release, mirror_url)
                # Try to download the Release.gpg file, in the assumption that
                # this file should always exist and is more or less guaranteed
                # to be relatively small.
                try:
                    mirror = CandidateMirror(mirror_url=mirror_url, updater=self)
                    mirror.release_gpg_contents = fetch_url(mirror.release_gpg_url, retry=False)
                    value = (MirrorStatus.AVAILABLE if mirror.is_available else MirrorStatus.UNAVAILABLE)
                except NotFoundError:
                    # When the mirror is serving 404 responses it can be an
                    # indication that the release has gone end of life. In any
                    # case the mirror is unavailable.
                    value = MirrorStatus.MAYBE_EOL
                except Exception:
                    # When we get an unspecified error that is not a 404
                    # response we conclude that the mirror is unavailable.
                    value = MirrorStatus.UNAVAILABLE
                # Cache the mirror status that we just determined.
                self.validated_mirrors[key] = value
            return value


class CandidateMirror(PropertyManager):

    """A candidate mirror groups a mirror URL with its availability and performance metrics."""

    @mutable_property
    def bandwidth(self):
        """
        The bytes per second achieved while fetching :attr:`release_gpg_url` (a number or :data:`None`).

        The value of this property is computed based on the values of
        :attr:`release_gpg_contents` and :attr:`release_gpg_latency`.
        """
        if self.release_gpg_contents and self.release_gpg_latency:
            return len(self.release_gpg_contents) / self.release_gpg_latency

    @lazy_property
    def archive_update_in_progress_url(self):
        """
        The URL of the file whose existence indicates that the mirror is being updated (a string).

        The value of this property is computed based on the value of
        :attr:`mirror_url`.
        """
        return '%s/Archive-Update-in-Progress-%s' % (
            self.mirror_url, urlparse(self.mirror_url).netloc,
        )

    @key_property
    def mirror_url(self):
        """The base URL of the mirror (a string)."""

    @mirror_url.setter
    def mirror_url(self, value):
        """Normalize the mirror URL when set."""
        set_property(self, 'mirror_url', normalize_mirror_url(value))

    @mutable_property
    def is_available(self):
        """
        :data:`True` if :attr:`release_gpg_contents` contains the expected header, :data:`False` otherwise.

        The value of this property is computed by checking whether
        :attr:`release_gpg_contents` contains the expected ``BEGIN PGP
        SIGNATURE`` header. This may seem like a rather obscure way of
        validating a mirror, but it was specifically chosen to detect
        all sorts of ways in which mirrors can be broken:

        - Webservers with a broken configuration that return an error page for
          all URLs.

        - Mirrors whose domain name registration has expired, where the domain
          is now being squatted and returns HTTP 200 OK responses for all URLs
          (whether they "exist" or not).
        """
        value = False
        if self.release_gpg_contents:
            value = b'BEGIN PGP SIGNATURE' in self.release_gpg_contents
            if not value:
                logger.debug("Missing GPG header, considering mirror unavailable (%s).", self.release_gpg_url)
            set_property(self, 'is_available', value)
        return value

    @mutable_property
    def is_updating(self):
        """:data:`True` if the mirror is being updated, :data:`False` otherwise."""

    @mutable_property
    def last_updated(self):
        """The time in seconds since the most recent mirror update (a number or :data:`None`)."""

    @mutable_property
    def release_gpg_contents(self):
        """
        The contents downloaded from :attr:`release_gpg_url` (a string or :data:`None`).

        By downloading the file available at :attr:`release_gpg_url` and
        setting :attr:`release_gpg_contents` and :attr:`release_gpg_latency`
        you enable the :attr:`bandwidth` and :attr:`is_available` properties to
        be computed.
        """

    @mutable_property
    def release_gpg_latency(self):
        """
        The time it took to download :attr:`release_gpg_url` (a number or :data:`None`).

        By downloading the file available at :attr:`release_gpg_url` and
        setting :attr:`release_gpg_contents` and :attr:`release_gpg_latency`
        you enable the :attr:`bandwidth` and :attr:`is_available` properties to
        be computed.
        """

    @mutable_property
    def release_gpg_url(self):
        """
        The URL of the ``Release.gpg`` file that will be used to test the mirror (a string or :data:`None`).

        The value of this property is based on :attr:`mirror_url` and the
        :attr:`~AptMirrorUpdater.distribution_codename` property of the
        :attr:`updater` object.
        """
        if self.updater and self.updater.distribution_codename:
            return '%s/dists/%s/Release.gpg' % (
                self.mirror_url, self.updater.distribution_codename,
            )

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

    @mutable_property(repr=False)
    def updater(self):
        """A reference to the :class:`AptMirrorUpdater` object that created the candidate."""


class MirrorStatus(Enum):

    """Enumeration for mirror statuses determined by :func:`AptMirrorUpdater.validate_mirror()`."""

    AVAILABLE = 1
    """The mirror is accepting connections and serving the expected content."""

    MAYBE_EOL = 2
    """The mirror is serving HTTP 404 "Not Found" responses instead of the expected content."""

    UNAVAILABLE = 3
    """The mirror is not accepting connections or not serving the expected content."""


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


def mirrors_are_equal(a, b):
    """
    Check whether two mirror URLS are equal.

    :param a: The first mirror URL (a string).
    :param b: The second mirror URL (a string).
    :returns: :data:`True` if the mirror URLs are equal,
              :data:`False` otherwise.
    """
    return normalize_mirror_url(a) == normalize_mirror_url(b)


def normalize_mirror_url(url):
    """
    Normalize a mirror URL so it can be compared using string equality comparison.

    :param url: The mirror URL to normalize (a string).
    :returns: The normalized mirror URL (a string).
    """
    return url.rstrip('/')
