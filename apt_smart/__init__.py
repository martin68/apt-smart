# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: martin68 and Peter Odding
# Last Change: May 31, 2020
# URL: https://apt-smart.readthedocs.io

"""
Automated, robust ``apt-get`` mirror selection for Debian and Ubuntu.

The main entry point for this module is the :class:`AptMirrorUpdater` class, so
if you don't know where to start that would be a good place :-). You can also
take a look at the source code of the :mod:`apt_smart.cli` module for
an example that uses the :class:`AptMirrorUpdater` class.
"""

# Standard library modules.
import fnmatch
import logging
import os
import sys
import time
import calendar

# Python 2.x / 3.x compatibility.
try:
    from enum import Enum
except ImportError:
    from flufl.enum import Enum

# External dependencies.
from capturer import CaptureOutput
from executor.contexts import ChangeRootContext, LocalContext
from humanfriendly import AutomaticSpinner, Timer, compact, format_timespan, pluralize
try:
    from property_manager3 import (
        PropertyManager,
        cached_property,
        key_property,
        lazy_property,
        mutable_property,
        set_property,
    )
except ImportError:
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
from apt_smart.http import NotFoundError, fetch_concurrent, fetch_url, get_default_concurrency
from apt_smart.releases import coerce_release
from apt_smart.releases import discover_releases

# Semi-standard module versioning.
__version__ = '7.1.3'

SOURCES_LIST_ENCODING = 'UTF-8'
"""The text encoding of :attr:`main_sources_list` (a string)."""

MAX_MIRRORS = 50
"""A sane default value for :attr:`AptMirrorUpdater.max_mirrors`."""

URL_CHAR_LEN = 34
"""A default value for :attr:`AptMirrorUpdater.url_char_len`."""

LAST_UPDATED_DEFAULT = 60 * 60 * 24 * 7 * 4
"""A default, pessimistic :attr:`~CandidateMirror.last_updated` value (a number)."""

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


class AptMirrorUpdater(PropertyManager):

    """Python API for the `apt-smart` program."""

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

    In the case of `apt-smart` this behavior would trigger external
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
        """A list of :class:`CandidateMirror` objects (ordered from best to worst)"""
        mirrors = set()
        if self.release_is_eol:
            logger.warning("Skipping mirror discovery because %s is EOL.", self.release)
        else:
            if self.read_custom_mirror_file:
                mirrors.update(self.read_custom_mirror_file)
                logger.info("Custom mirrors added from file:")
                for mirror in mirrors:
                    logger.info(mirror.mirror_url)
            logger.info("Adding BASE_URL mirror:")
            if self.distributor_id == 'debian':  # For Debian, base_url typically is not in MIRRORS_URL,
                # add it explicitly
                base_url_prefix = self.backend.BASE_URL.split('/dists/codename-updates/Release')[0]
                mirrors.add(CandidateMirror(mirror_url=base_url_prefix, updater=self))
            elif self.distributor_id == 'ubuntu':  # For Ubuntu, base_url is not in MIRRORS_URL for
                # some countries e.g. US (found it in Travis CI), add it explicitly
                base_url_prefix = self.backend.BASE_URL.split('/dists/codename-security/Release')[0]
                mirrors.add(CandidateMirror(mirror_url=base_url_prefix, updater=self))
            elif self.distributor_id == 'linuxmint':  # For Linux Mint, base_url typically is not in MIRRORS_URL,
                # add it explicitly
                base_url_prefix = self.backend.BASE_URL.split('/dists/codename/Release')[0]
                mirrors.add(CandidateMirror(mirror_url=base_url_prefix, updater=self))
            logger.info(base_url_prefix)
            for candidate in self.backend.discover_mirrors():
                if any(fnmatch.fnmatch(candidate.mirror_url, pattern) for pattern in self.blacklist)\
                        and normalize_mirror_url(candidate.mirror_url) != base_url_prefix:
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
            # if self.distributor_id == self.context.distributor_id: # We don't need to check this since
            # 60850cc2 commint (Reimplement more robust :attr:`distribution_codename` using APT sources.list)
            # already using :attr:`context` and self.context.distributor_id has issue:
            # https://github.com/xolox/python-executor/issues/17
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

    @mutable_property(cached=True)
    def current_mirror(self):
        """
        The URL of the main mirror in use in :attr:`main_sources_list` (a string).

        The :attr:`current_mirror` property's value is computed using
        :func:`find_current_mirror()`, but can be changed and cached by :func:`distribution_codename`
        for Linux Mint's Ubuntu Mode.
        """
        if self.ubuntu_mode and self.distribution_codename:  # :func:`distribution_codename` will set current_mirror
            return self.current_mirror
        else:
            logger.debug("Parsing %s to find current mirror of %s ..", self.main_sources_list, self.context)
            return find_current_mirror(self.get_sources_list())

    @mutable_property
    def distribution_codename_old(self):
        """
        deprecated: The distribution codename (a lowercase string like 'trusty' or 'xenial').

        This relies on :mod:`executor` which is not robust to detect codename when
        neither /etc/lsb-release nor lsb_release command are available, e.g. the official
        Debian docker image (see https://github.com/xolox/python-executor/issues/17 )

        The value of this property defaults to the value of the
        :attr:`executor.contexts.AbstractContext.distribution_codename`
        property which is the right choice 99% of the time.
        """
        return self.context.distribution_codename

    @mutable_property(cached=True)
    def distribution_codename(self):
        """
        The distribution codename (a lowercase string like 'trusty' or 'xenial')

        The value of this property is determined using APT sources.list and should be more robust.
        Similar to :func:`find_current_mirror` but return token[2] instead.
        Also refer code of :func:`coerce_release`.

        """
        for line in self.get_sources_list().splitlines():
            # The first token should be `deb' or `deb-src', the second token is
            # the mirror's URL, the third token is the `distribution' and any
            # further tokens are `components'.
            tokens = line.split()
            if (len(tokens) >= 4
                    and tokens[0] in ('deb', 'deb-src')
                    and tokens[1].startswith(('http://', 'https://', 'ftp://', 'mirror://', 'mirror+file:/'))
                    and 'main' in tokens[3:]):
                matches = [release for release in discover_releases() if tokens[2].lower() in release.codename.lower()]
                if len(matches) != 1:
                    continue
                if self.ubuntu_mode and matches[0].distributor_id == 'linuxmint':
                    self.current_mirror = tokens[1]
                    continue
                if self.ubuntu_mode:
                    logging.info("In Ubuntu Mode, pretend to be %s" % coerce_release(tokens[2]))
                return tokens[2]
        raise EnvironmentError("Failed to determine the distribution codename using apt's package resource list!")

    @mutable_property(cached=True)
    def distributor_id(self):
        """
        The distributor ID (a lowercase string like 'debian' or 'ubuntu').

        The default value of this property is based on the
        :attr:`~apt_smart.releases.Release.distributor_id` property of
        :attr:`release` (which in turn is based on :attr:`distribution_codename`).

        Because Debian and Ubuntu code names are unambiguous this means that in
        practice you can provide a value for :attr:`distribution_codename` and
        omit :attr:`distributor_id` and everything should be fine.
        """
        return self.release.distributor_id

    @cached_property
    def main_sources_list(self):
        """
        The absolute pathname of the list of configured APT data sources (a string).

        For new version of Linux Mint, main_sources_list is:
        /etc/apt/sources.list.d/official-package-repositories.list
        """
        if self.context.exists('/etc/apt/sources.list.d/official-package-repositories.list'):
            logger.debug("/etc/apt/sources.list.d/official-package-repositories.list exists,\
                         use it as main_sources_list instead of /etc/apt/sources.list")
            return '/etc/apt/sources.list.d/official-package-repositories.list'
        else:
            return '/etc/apt/sources.list'

    @mutable_property
    def max_mirrors(self):
        """Limits the number of mirrors to rank (a number, defaults to :data:`MAX_MIRRORS`)."""
        return MAX_MIRRORS

    @mutable_property
    def url_char_len(self):
        """
        The length of chars in mirrors' URL to display(a number, defaults to :data:`URL_CHAR_LEN`)

        Specify the length of chars in mirrors' URL to display when using --list-mirrors
        """
        return URL_CHAR_LEN

    @mutable_property
    def ubuntu_mode(self):
        """
        For Linux Mint, deal with upstream Ubuntu mirror instead of Linux Mint mirror if True

        Default is False, can be set True via -U, --ubuntu flag
        """
        return False

    @mutable_property
    def old_releases_url(self):
        """The URL of the mirror that serves old releases for this :attr:`backend` (a string)."""
        return self.backend.OLD_RELEASES_URL

    @mutable_property
    def base_url(self):
        """The actual official base URL according to :data:`.BASE_URL`"""
        return self.backend.BASE_URL.replace('codename', self.distribution_codename)

    @mutable_property
    def base_last_updated(self):
        """
        The Unix timestamp to determine which mirrors are up-to-date (an int)

        The value of this property is gotten from :attr:`base_url`'s update date as minuend
        """

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
        # NO, we don't need to now since the backends.debian can smartly get mirrors within a country.
        # Without max_mirrors limit we can fix errors within United States (Travis CI reported) where
        # where we can get 80+ mirrors. If limit applies, base_url mirror may be deleted, then error occurs.
        """
        if self.max_mirrors and len(mirrors) > self.max_mirrors:
            mirrors = mirrors[:self.max_mirrors]
        """
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

        logger.info("Start retrieving :attr:`base_last_updated` using is_available")
        self.base_last_updated = 0
        if self.release_is_eol:
            self.base_last_updated = int(time.time())
            logger.warning("%s is EOL, so using time.time() as :attr:`base_last_updated`: %i",
                           self.release, self.base_last_updated)
        elif mapping[self.base_url].is_available:
            logger.debug(":attr:`base_last_updated`: %i", self.base_last_updated)
            # base_url 's contents are up-to-date naturally,so set its last_updated 0
            mapping[self.base_url].last_updated = 0
        else:  # base_url not available, use time at the moment as base_last_updated.
            self.base_last_updated = int(time.time())
            logger.warning("%s is not available, so using time.time() as :attr:`base_last_updated`: %i",
                           self.base_url, self.base_last_updated)
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
        # blacklist BASE_URL mirror if matches blacklist pattern
        if any(fnmatch.fnmatch(mapping[self.base_url].mirror_url, pattern) for pattern in self.blacklist):
            logger.warning("Ignoring blacklisted BASE_URL mirror %s.", mapping[self.base_url].mirror_url)
            mirrors.remove(mapping[self.base_url])
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
            function (only the :mod:`~apt_smart.backends.debian`
            module does at the time of writing) then it is called and if it
            returns a number, this number is the EOL date for the release.

            This function was added to enable apt-smart backend
            modules to override the default EOL dates, more specifically to
            respect the `Debian LTS`_ release schedule (see also `issue #5`_).

          - Otherwise the :attr:`~apt_smart.releases.Release.eol_date`
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
        if release_is_eol and self.distributor_id == 'linuxmint':
            logger.info(
                "%s seems EOL (based on %s), but for Linux Mint no OLD_RELEASES_URL, so act as not EOL.",
                self.release, source,
            )
            release_is_eol = False
            return release_is_eol
        if release_is_eol:  # Still need to check due to
            # https://github.com/xolox/python-apt-mirror-updater/issues/9
            logger.info("%s seems EOL, checking %s MirrorStatus to confirm.", self.release, self.old_releases_url)
            release_is_eol = (self.validate_mirror(self.old_releases_url) == MirrorStatus.AVAILABLE)
            if not release_is_eol:
                source = "%s is not available" % self.old_releases_url
        logger.info(
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

    @mutable_property
    def custom_mirror_file_path(self):
        """The local custom mirror file's absolute path, can be set by `-F` flag"""
        return None

    @cached_property
    def read_custom_mirror_file(self):
        """
        Read a file containing custom mirror URLs  (one URL per line) to add custom mirrors to rank.

        :param file_to_read: The local file's absolute path
        :returns: A set of mirrors read from file
        """
        if self.custom_mirror_file_path is None:
            return {}
        else:
            logger.info("The file path you input is %s", self.custom_mirror_file_path)
            mirrors = set()
            with open(self.custom_mirror_file_path) as f:
                for line in f:
                    if line.strip().startswith(('http://', 'https://', 'ftp://')):
                        mirrors.add(CandidateMirror(mirror_url=line.strip(), updater=self))

            return mirrors

    def change_mirror(self, new_mirror=None, update=True):
        """
        Change the main mirror in use in :attr:`main_sources_list`.

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
        mirrors_to_replace = [normalize_mirror_url(self.current_mirror)]
        if self.release_is_eol:
            # When a release goes EOL the security updates mirrors stop
            # serving that release as well, so we need to remove them.
            logger.debug("Replacing %s URLs as well ..", self.security_url)
            mirrors_to_replace.append(normalize_mirror_url(self.security_url))
        else:
            logger.debug("Not replacing %s URLs.", self.security_url)
        lines = sources_list.splitlines()
        sources_list_options = self.get_sources_list_options
        for i, line in enumerate(lines):
            # The first token should be `deb' or `deb-src', the second token is
            # the mirror's URL, the third token is the `distribution' and any
            # further tokens are `components'.
            tokens = line.split()
            if (len(tokens) >= 4
                    and tokens[0] in ('deb', 'deb-src')
                    and normalize_mirror_url(tokens[1]) in mirrors_to_replace):
                tokens[1] = new_mirror
                if i in sources_list_options:
                    tokens.insert(1, '[' + sources_list_options[i] + ']')  # Get the [options] back
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

    def create_chroot(self, directory, codename=None, arch=None):
        """
        Bootstrap a basic Debian or Ubuntu system using debootstrap_.

        :param directory: The pathname of the target directory (a string).
        :param codename: The codename of the target (a string).
        :param arch: The target architecture (a string or :data:`None`).
        :returns: A :class:`~executor.contexts.ChangeRootContext` object.

        If `directory` already exists and isn't empty then it is assumed that
        the chroot has already been created and debootstrap_ won't be run.
        Before this method returns it changes :attr:`context` to the chroot.

        .. _debootstrap: https://manpages.debian.org/debootstrap
        """
        logger.debug("Checking if chroot already exists (%s) ..", directory)
        if self.context.exists(directory) and self.context.list_entries(directory):
            logger.info("The chroot already exists, skipping initialization.")
            first_run = False
        else:
            # Ensure the `debootstrap' program is installed.
            if not self.context.find_program('debootstrap'):
                logger.info("Installing `debootstrap' program ..")
                self.context.execute('apt-get', 'install', '--yes', 'debootstrap', sudo=True)
            # Use the `debootstrap' program to create the chroot.
            timer = Timer()
            debootstrap_command = ['debootstrap']
            if arch:
                debootstrap_command.append('--arch=%s' % arch)
            release_chroot = None
            keyring_chroot = ''
            codename_chroot = ''
            best_mirror_chroot = None
            generate_sources_list_chroot = None
            if codename and codename != self.distribution_codename:
                updater_chroot = AptMirrorUpdater()
                updater_chroot.distribution_codename = codename
                if updater_chroot.distributor_id == 'linuxmint':
                    msg = "It seems no sense to create chroot of Linux Mint, " \
                          "please specify a codename of Ubuntu or Debian " \
                          "to create chroot."
                    raise ValueError(msg)

                if not self.context.exists(updater_chroot.release.keyring_file):
                    if updater_chroot.distributor_id == 'ubuntu':
                        self.context.execute('apt-get', 'install', '--yes', 'ubuntu-keyring', sudo=True)
                    elif updater_chroot.distributor_id == 'debian':
                        self.context.execute('apt-get', 'install', '--yes', 'debian-archive-keyring', sudo=True)
                release_chroot = updater_chroot.release
                keyring_chroot = updater_chroot.release.keyring_file
                codename_chroot = codename
                best_mirror_chroot = updater_chroot.best_mirror
            else:
                if self.distributor_id == 'linuxmint':
                    msg = "It seems no sense to create chroot of Linux Mint, " \
                          "please use -C to specify a codename of Ubuntu or Debian " \
                          "to create chroot."
                    raise ValueError(msg)
                release_chroot = self.release
                keyring_chroot = self.release.keyring_file
                codename_chroot = self.distribution_codename
                best_mirror_chroot = self.best_mirror
            logger.info("Creating %s chroot in %s ..", release_chroot, directory)
            debootstrap_command.append('--keyring=%s' % keyring_chroot)
            debootstrap_command.append(codename_chroot)
            debootstrap_command.append(directory)
            debootstrap_command.append(best_mirror_chroot)
            self.context.execute(*debootstrap_command, sudo=True)
            logger.info("Took %s to create %s chroot.", timer, release_chroot)
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
        if codename and codename != self.distribution_codename:
            updater_chroot.context = self.context
            del updater_chroot.current_mirror
            del updater_chroot.stable_mirror
            generate_sources_list_chroot = updater_chroot.generate_sources_list()
        else:
            generate_sources_list_chroot = self.generate_sources_list()
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
            logger.debug("sources.list for chroot generated:")
            logger.debug(generate_sources_list_chroot)
            self.install_sources_list(generate_sources_list_chroot)
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
        (:func:`apt_smart.backends.debian.generate_sources_list()`)
        and Ubuntu (:func:`apt_smart.backends.ubuntu.generate_sources_list()`)
        backend implementations of this method for details on argument handling
        and the return value.
        """
        if options.get('mirror_url') is None:
            options['mirror_url'] = self.stable_mirror
        options.setdefault('codename', self.distribution_codename)
        return self.backend.generate_sources_list(**options)

    @mutable_property
    def get_sources_list_options(self):
        """
        Get the contents of [options] in :attr:`main_sources_list`.

        [options] can be set into sources.list, e.g.
        deb [arch=amd64] http://mymirror/ubuntu bionic main restricted
        see details at
        https://manpages.debian.org/jessie/apt/sources.list.5.en.html
        The [options] is often not considered and breaks parsing in many projects, see
        https://github.com/jblakeman/apt-select/issues/54
        We begin to deal with the [options] by stripping it from sources.list,
        and then get it back when generating new sources.list
        """

    def get_sources_list(self):
        """
        Get the contents of :attr:`main_sources_list`.

        :returns: A Unicode string.

        This code currently assumes that the ``sources.list`` file is encoded
        using :data:`SOURCES_LIST_ENCODING`. I'm not actually sure if this is
        correct because I haven't been able to find a formal specification!
        Feedback is welcome :-).
        This code strips [options] from sources.list, stores it in :attr:`get_sources_list_options`
        """
        contents = self.context.read_file(self.main_sources_list)
        contents = contents.decode(SOURCES_LIST_ENCODING)
        sources_list_options = {}
        contents_raw = []  # stripped contents without options
        for i, line in enumerate(contents.splitlines()):
            if line.find('[') > 0:  # found '[' and not starts with '['
                startswith_deb = line.split('[')[0]
                temp = line.split('[')[1]
                sources_list_options[i] = temp.split(']')[0]
                startswith_http = temp.split(']')[1]
                contents_raw.append(startswith_deb + startswith_http)
            elif line.find('[') == -1:  # not found
                contents_raw.append(line)
        self.get_sources_list_options = sources_list_options
        return '\n'.join(contents_raw)

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
        logger.info("Installing new %s ..", self.main_sources_list)
        with self.context:
            # Write the sources.list contents to a temporary file. We make sure
            # the file always ends in a newline to adhere to UNIX conventions.
            temporary_file = '/tmp/apt-smart-sources-list-%i.txt' % os.getpid()
            contents_to_write = contents.rstrip() + b'\n'
            self.context.write_file(temporary_file, contents_to_write)
            # Make sure the temporary file is cleaned up when we're done with it.
            self.context.cleanup('rm', '--force', temporary_file)
            # Make a backup copy of /etc/apt/sources.list in case shit hits the fan?
            if self.context.exists(self.main_sources_list):
                dirname, basename = os.path.split(self.main_sources_list)
                if basename == 'official-package-repositories.list':
                    backup_dir = os.path.join(dirname, 'backup_by_apt-smart')  # Backup to dir for Linux Mint
                    if not self.context.exists(backup_dir):
                        self.context.execute('mkdir', backup_dir, sudo=True)
                    backup_copy = '%s.backup.%i' % (os.path.join(backup_dir, basename), time.time())
                else:
                    backup_copy = '%s.backup.%i' % (self.main_sources_list, time.time())
                logger.info("Backing up contents of %s to %s ..", self.main_sources_list, backup_copy)
                self.context.execute('cp', self.main_sources_list, backup_copy, sudo=True)
            # Move the temporary file into place without changing ownership and permissions.
            self.context.execute(
                'cp', '--no-preserve=mode,ownership',
                temporary_file, self.main_sources_list,
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

        """
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
        :data:`True` if :attr:`release_gpg_contents` contains the expected data, :data:`False` otherwise.

        The value of this property is computed by checking whether
        :attr:`release_gpg_contents` contains the expected data.
        This may seem like a rather obscure way of
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
            value = b'Date:' in self.release_gpg_contents
            if not value:
                logger.debug("Missing data, considering mirror unavailable (%s).", self.release_gpg_url)
            else:
                # Get all data following "Date: "
                date_string_raw = self.release_gpg_contents.decode('utf-8').split("Date: ", 1)
                if len(date_string_raw) == 2:  # split succussfully using "Date: "
                    # Get only date string like "Sun, 25 Aug 2019 23:35:36 UTC", drop other data
                    date_string = date_string_raw[1].split("\n")[0]
                    if date_string.endswith("UTC"):
                        # Convert it into UNIX timestamp
                        last_updated_time = calendar.timegm(time.strptime(date_string, "%a, %d %b %Y %H:%M:%S %Z"))
                        if self.updater.base_last_updated == 0:  # First time launch this method, must be base_url
                            self.updater.base_last_updated = last_updated_time
                            logger.debug("base_last_updated: %i", self.updater.base_last_updated)
                        else:
                            # if last_updated is 0 means this mirror is up-to-date
                            self.last_updated = self.updater.base_last_updated - last_updated_time
                            logger.debug("last_updated: %i", self.last_updated)
                    else:
                        logger.debug("Not UTC? Correct me. " + date_string)
                    logger.debug("Looks good, %s is_available return True", self.release_gpg_url)
                else:  # split fails because lacking "Date: "
                    logger.debug("Missing Date, considering mirror unavailable (%s).", self.release_gpg_url)
                    value = False
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
        The URL of the ``Release`` file that will be used to test the mirror (a string or :data:`None`).

        The value of this property is based on :attr:`mirror_url` and the
        :attr:`~AptMirrorUpdater.distribution_codename` property of the
        :attr:`updater` object.
        """
        if self.updater and self.updater.distribution_codename:
            if self.updater.distributor_id == 'ubuntu':
                return '%s/dists/%s-security/Release' % (
                    self.mirror_url, self.updater.distribution_codename,
                )
            elif self.updater.distributor_id == 'debian':
                return '%s/dists/%s-updates/Release' % (
                    self.mirror_url, self.updater.distribution_codename,
                )
            elif self.updater.distributor_id == 'linuxmint':
                return '%s/dists/%s/Release' % (
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
                         contents of :attr:`main_sources_list` (a string).
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
        if (len(tokens) >= 4
                and tokens[0] in ('deb', 'deb-src')
                and tokens[1].startswith(('http://', 'https://', 'ftp://', 'mirror://', 'mirror+file:/'))
                and 'main' in tokens[3:]):
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
