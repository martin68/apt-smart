# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 14, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Test suite for the ``apt-mirror-updater`` package."""

# Standard library modules.
import logging
import os
import sys
import unittest

# External dependencies.
import coloredlogs
from executor import execute
from humanfriendly import compact
from six.moves import StringIO

# Modules included in our package.
from apt_mirror_updater import AptMirrorUpdater
from apt_mirror_updater.cli import main

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


class AptMirrorUpdaterTestCase(unittest.TestCase):

    """:mod:`unittest` compatible container for the :mod:`apt_mirror_updater` test suite."""

    def setUp(self):
        """Enable verbose logging and reset it after each test."""
        coloredlogs.install(level='DEBUG')

    def skipTest(self, text, *args, **kw):
        """
        Enable backwards compatible "marking of tests to skip".

        By calling this method from a return statement in the test to be
        skipped the test can be marked as skipped when possible, without
        breaking the test suite when unittest.TestCase.skipTest() isn't
        available.
        """
        reason = compact(text, *args, **kw)
        try:
            super(AptMirrorUpdaterTestCase, self).skipTest(reason)
        except AttributeError:
            # unittest.TestCase.skipTest() isn't available in Python 2.6.
            logger.warning("%s", reason)

    def test_debian_mirror_discovery(self):
        """Test the discovery of Debian mirror URLs."""
        from apt_mirror_updater.backends.debian import discover_mirrors
        mirrors = discover_mirrors()
        assert len(mirrors) > 10
        assert all(is_debian_mirror(c.mirror_url) for c in mirrors)

    def test_ubuntu_mirror_discovery(self):
        """Test the discovery of Ubuntu mirror URLs."""
        from apt_mirror_updater.backends.ubuntu import discover_mirrors
        mirrors = discover_mirrors()
        assert len(mirrors) > 10
        assert all(is_ubuntu_mirror(c.mirror_url) for c in mirrors)

    def test_adaptive_mirror_discovery(self):
        """Test the discovery of mirrors for the current type of system."""
        updater = AptMirrorUpdater()
        assert len(updater.available_mirrors) > 10
        assert all(is_mirror_url(c.mirror_url) for c in updater.available_mirrors)

    def test_mirror_ranking(self):
        """Test the ranking of discovered mirrors."""
        updater = AptMirrorUpdater()
        # Make sure that multiple discovered mirrors are available.
        assert sum(m.is_available for m in updater.ranked_mirrors) > 10

    def test_best_mirror_selection(self):
        """Test the selection of a "best" mirror."""
        updater = AptMirrorUpdater()
        assert is_mirror_url(updater.best_mirror)

    def test_current_mirror_discovery(self):
        """Test that the current mirror can be extracted from ``/etc/apt/sources.list``."""
        exit_code, output = run_cli('--find-current-mirror')
        assert exit_code == 0
        assert is_mirror_url(output.strip())

    def test_dumb_update(self):
        """Test that our dumb ``apt-get update`` wrapper works."""
        if os.getuid() != 0:
            return self.skipTest("root privileges required to opt in")
        updater = AptMirrorUpdater()
        # Remove all existing package lists.
        updater.clear_package_lists()
        # Verify that package lists aren't available.
        assert not have_package_lists()
        # Run `apt-get update' to download the package lists.
        updater.dumb_update()
        # Verify that package lists are again available.
        assert have_package_lists()

    def test_smart_update(self):
        """
        Test that our smart ``apt-get update`` wrapper works.

        Currently this test simply ensures coverage of the happy path.
        Ideally it will evolve to test the handled edge cases as well.
        """
        if os.getuid() != 0:
            return self.skipTest("root privileges required to opt in")
        updater = AptMirrorUpdater()
        # Remove all existing package lists.
        updater.clear_package_lists()
        # Verify that package lists aren't available.
        assert not have_package_lists()
        # Run `apt-get update' to download the package lists.
        updater.smart_update()
        # Verify that package lists are again available.
        assert have_package_lists()


def have_package_lists():
    """
    Check if apt's package lists are available.

    :returns: :data:`True` when package lists are available,
              :data:`False` otherwise.

    This function checks that the output of ``apt-cache show python`` contains
    a ``Filename: ...`` key/value pair which indicates that apt knows where to
    download the package archive that installs the ``python`` package.
    """
    return 'Filename:' in execute('apt-cache', 'show', 'python', check=False, capture=True)


def is_mirror_url(url):
    """Check whether the given URL looks like a Debian or Ubuntu mirror URL."""
    return is_debian_mirror(url) or is_ubuntu_mirror(url)


def is_debian_mirror(url):
    """Check whether the given URL looks like a Debian mirror URL."""
    return has_compatible_scheme(url) and url.endswith('/debian/')


def is_ubuntu_mirror(url):
    """Check whether the given URL looks like a Ubuntu mirror URL."""
    return has_compatible_scheme(url) and url.endswith('/ubuntu/')


def has_compatible_scheme(url):
    """Check whether the given URL uses a scheme compatible with and intended to be used by apt."""
    return url.startswith(('http://', 'https://'))


def run_cli(*arguments):
    """Simple wrapper to run :func:`apt_mirror_updater.cli.main()` in the same process."""
    saved_argv = sys.argv
    saved_stderr = sys.stderr
    saved_stdout = sys.stdout
    fake_stdout = StringIO()
    try:
        sys.argv = ['apt-mirror-updater'] + list(arguments)
        sys.stdout = fake_stdout
        sys.stderr = fake_stdout
        main()
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code
    finally:
        sys.argv = saved_argv
        sys.stderr = saved_stderr
        sys.stdout = saved_stdout
    return exit_code, fake_stdout.getvalue()
