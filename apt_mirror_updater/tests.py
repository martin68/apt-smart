# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 7, 2018
# URL: https://apt-mirror-updater.readthedocs.io

"""Test suite for the ``apt-mirror-updater`` package."""

# Standard library modules.
import logging
import os
import time

# External dependencies.
from executor import execute
from executor.contexts import LocalContext
from humanfriendly.testing import TestCase, run_cli
from humanfriendly.text import split

# Modules included in our package.
from apt_mirror_updater import AptMirrorUpdater, normalize_mirror_url
from apt_mirror_updater.cli import main
from apt_mirror_updater.eol import DISTRO_INFO_DIRECTORY, gather_eol_dates

# Initialize a logger for this module.
logger = logging.getLogger(__name__)


class AptMirrorUpdaterTestCase(TestCase):

    """:mod:`unittest` compatible container for the :mod:`apt_mirror_updater` test suite."""

    def test_debian_mirror_discovery(self):
        """Test the discovery of Debian mirror URLs."""
        from apt_mirror_updater.backends.debian import discover_mirrors
        mirrors = discover_mirrors()
        assert len(mirrors) > 10
        for candidate in mirrors:
            check_debian_mirror(candidate.mirror_url)

    def test_ubuntu_mirror_discovery(self):
        """Test the discovery of Ubuntu mirror URLs."""
        from apt_mirror_updater.backends.ubuntu import discover_mirrors
        mirrors = discover_mirrors()
        assert len(mirrors) > 10
        for candidate in mirrors:
            check_ubuntu_mirror(candidate.mirror_url)

    def test_adaptive_mirror_discovery(self):
        """Test the discovery of mirrors for the current type of system."""
        updater = AptMirrorUpdater()
        assert len(updater.available_mirrors) > 10
        for candidate in updater.available_mirrors:
            check_mirror_url(candidate.mirror_url)

    def test_mirror_ranking(self):
        """Test the ranking of discovered mirrors."""
        updater = AptMirrorUpdater()
        # Make sure that multiple discovered mirrors are available.
        assert sum(m.is_available for m in updater.ranked_mirrors) > 10

    def test_best_mirror_selection(self):
        """Test the selection of a "best" mirror."""
        updater = AptMirrorUpdater()
        check_mirror_url(updater.best_mirror)

    def test_current_mirror_discovery(self):
        """Test that the current mirror can be extracted from ``/etc/apt/sources.list``."""
        exit_code, output = run_cli(main, '--find-current-mirror')
        assert exit_code == 0
        check_mirror_url(output.strip())

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

    def test_gather_eol_dates(self):
        """Test that gathering of EOL dates works properly."""
        if not os.path.exists(DISTRO_INFO_DIRECTORY):
            return self.skipTest("distro-info-data not available")
        dates = gather_eol_dates(context=LocalContext())
        assert len(dates) >= 2
        assert 'debian' in dates
        assert 'ubuntu' in dates
        assert len(dates['debian']) > 0
        assert len(dates['ubuntu']) > 0

    def test_debian_lts_eol_date(self):
        """
        Regression test for `issue #5`_.

        .. _issue #5: https://github.com/xolox/python-apt-mirror-updater/issues/5
        """
        updater = AptMirrorUpdater(
            distributor_id='debian',
            distribution_codename='jessie',
            architecture='amd64',
        )
        eol_expected = (time.time() >= 1593468000)
        assert updater.release_is_eol == eol_expected


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


def check_mirror_url(url):
    """Check whether the given URL looks like a Debian or Ubuntu mirror URL."""
    if not (is_debian_mirror(url) or is_ubuntu_mirror(url)):
        msg = "Invalid mirror URL! (%r)"
        raise AssertionError(msg % url)


def check_debian_mirror(url):
    """Ensure the given URL looks like a Debian mirror URL."""
    if not is_debian_mirror(url):
        msg = "Invalid Debian mirror URL! (%r)"
        raise AssertionError(msg % url)


def check_ubuntu_mirror(url):
    """Ensure the given URL looks like a Ubuntu mirror URL."""
    if not is_ubuntu_mirror(url):
        msg = "Invalid Ubuntu mirror URL! (%r)"
        raise AssertionError(msg % url)


def is_debian_mirror(url):
    """Check whether the given URL looks like a Debian mirror URL."""
    url = normalize_mirror_url(url)
    if has_compatible_scheme(url):
        components = split(url, '/')
        return components[-1] == 'debian'


def is_ubuntu_mirror(url):
    """Check whether the given URL looks like a Ubuntu mirror URL."""
    url = normalize_mirror_url(url)
    if has_compatible_scheme(url):
        components = split(url, '/')
        return components[-1] in ('ubuntu', 'archive.ubuntu.com')


def has_compatible_scheme(url):
    """Check whether the given URL uses a scheme compatible with and intended to be used by apt."""
    return url.startswith(('http://', 'https://'))
