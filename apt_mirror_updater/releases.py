# Easy to use metadata on Debian and Ubuntu releases.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 19, 2018
# URL: https://apt-mirror-updater.readthedocs.io

"""
Easy to use metadata on Debian and Ubuntu releases.

This module started out with the purpose of reliable `end of life`_ (EOL)
detection for Debian and Ubuntu releases based on data provided by the
distro-info-data_  package. Since then the need arose to access more of the
available metadata and so the ``eol`` module became the ``releases`` module.

Debian and Ubuntu releases have an EOL date that marks the end of support for
each release. At that date the release stops receiving further (security)
updates and some time after package mirrors stop serving the release.

The distro-info-data_ package contains CSV files with metadata about Debian and
Ubuntu releases. This module parses those CSV files to make this metadata
available in Python. This enables `apt-mirror-updater` to make an informed
decision about the following questions:

1. Is a given Debian or Ubuntu release expected to be available on mirrors or
   will it only be available in the archive of old releases?

2. Is the signing key of a given Ubuntu release expected to be included in the
   main keyring (:data:`UBUNTU_KEYRING_CURRENT`) or should the keyring with
   removed keys (:data:`UBUNTU_KEYRING_REMOVED`) be used?

To make it possible to run `apt-mirror-updater` without direct access to the
CSV files, a copy of the relevant information has been embedded in the source
code.

.. _end of life: https://en.wikipedia.org/wiki/End-of-life_(product)
.. _distro-info-data: https://packages.debian.org/distro-info-data
"""

# Standard library modules.
import csv
import datetime
import decimal
import glob
import logging
import numbers
import os

# External dependencies.
from executor import execute
from property_manager import PropertyManager, key_property, lazy_property, required_property, writable_property
from six import string_types

DISTRO_INFO_DIRECTORY = '/usr/share/distro-info'
"""The pathname of the directory with CSV files containing release metadata (a string)."""

DEBIAN_KEYRING_CURRENT = '/usr/share/keyrings/debian-keyring.gpg'
"""The pathname of the main Debian keyring file (a string)."""

UBUNTU_KEYRING_CURRENT = '/usr/share/keyrings/ubuntu-archive-keyring.gpg'
"""The pathname of the main Ubuntu keyring file (a string)."""

UBUNTU_KEYRING_REMOVED = '/usr/share/keyrings/ubuntu-archive-removed-keys.gpg'
"""The pathname of the Ubuntu keyring file with removed keys (a string)."""

# Public identifiers that require documentation.
__all__ = (
    'DISTRO_INFO_DIRECTORY',
    'DEBIAN_KEYRING_CURRENT',
    'UBUNTU_KEYRING_CURRENT',
    'UBUNTU_KEYRING_REMOVED',
    'Release',
    'coerce_release',
    'discover_releases',
    'ubuntu_keyring_updated',
)

# Initialize a logger.
logger = logging.getLogger(__name__)


def coerce_release(value):
    """
    Try to coerce the given value to a Debian or Ubuntu release.

    :param value: The value to coerce (a number, a string or a :class:`Release` object).
    :returns: A :class:`Release` object.
    :raises: :exc:`~exceptions.ValueError` when the given value cannot be coerced to a known release.

    The following values can be coerced:

    - Numbers and numbers formatted as strings match :attr:`Release.version`.
    - Strings match :attr:`Release.codename` (case insensitive).

    .. warning:: Don't use floating point numbers like 10.04 because their
                 actual value will be something like 10.039999999999999147
                 which won't match the intended release.
    """
    # Release objects pass through untouched.
    if isinstance(value, Release):
        return value
    # Numbers and version strings are matched against release versions.
    if isinstance(value, numbers.Number) or is_version_string(value):
        typed_value = decimal.Decimal(value)
        matches = [release for release in discover_releases() if release.version == typed_value]
        if len(matches) != 1:
            msg = "The number %s doesn't match a known Debian or Ubuntu release!"
            raise ValueError(msg % value)
        return matches[0]
    # Other strings are matched against release code names.
    matches = [release for release in discover_releases() if value.lower() in release.codename.lower()]
    if len(matches) != 1:
        msg = "The string %r doesn't match a known Debian or Ubuntu release!"
        raise ValueError(msg % value)
    return matches[0]


def discover_releases():
    """
    Discover known Debian and Ubuntu releases.

    :returns: A list of discovered :class:`Release` objects sorted by
             :attr:`~Release.distributor_id` and :attr:`~Release.version`.

    The first time this function is called it will try to parse the CSV files
    in ``/usr/share/distro-info`` and merge any releases it finds with the
    releases embedded into the source code of this module. The result is cached
    and returned each time the function is called. It's not a problem if the
    ``/usr/share/distro-info`` directory doesn't exist or doesn't contain any
    ``*.csv`` files (it won't cause a warning or error). Of course in this case
    only the embedded releases will be returned.
    """
    try:
        # Try to return the cached value.
        return discover_releases.cached_result
    except AttributeError:
        # Discover the known releases on the first call to discover_releases().
        # First we check the CSV files on the system where apt-mirror-updater
        # is running, because those files may be more up-to-date than the
        # bundled information is.
        result = set()
        for filename in glob.glob(os.path.join(DISTRO_INFO_DIRECTORY, '*.csv')):
            for release in parse_csv_file(filename):
                result.add(release)
        # Add the releases bundled with apt-mirror-updater to the result
        # without causing duplicate entries (due to the use of a set and key
        # properties).
        result.update(BUNDLED_RELEASES)
        # Sort the releases by distributor ID and version / series.
        result = sorted(result, key=lambda r: (r.distributor_id, r.version or 0, r.series))
        # Cache the resulting value.
        discover_releases.cached_result = result
        return result


def is_version_string(value):
    """Check whether the given value is a string containing a positive number."""
    try:
        return isinstance(value, string_types) and float(value) > 0
    except Exception:
        return False


def parse_csv_file(filename):
    """
    Parse a CSV file in the format of the ``/usr/share/distro-info/*.csv`` files.

    :param filename: The pathname of the CSV file (a string).
    :returns: A generator of :class:`Release` objects.
    """
    # We import this here to avoid a circular import.
    from apt_mirror_updater.backends.debian import LTS_RELEASES
    basename, extension = os.path.splitext(os.path.basename(filename))
    distributor_id = basename.lower()
    with open(filename) as handle:
        for entry in csv.DictReader(handle):
            yield Release(
                codename=entry['codename'],
                is_lts=(
                    entry['series'] in LTS_RELEASES if distributor_id == 'debian' else (
                        'LTS' in entry['version'] if distributor_id == 'ubuntu' else (
                            # Neither Debian nor Ubuntu, let's not assume anything...
                            False
                        ),
                    )
                ),
                created_date=parse_date(entry['created']),
                distributor_id=distributor_id,
                eol_date=parse_date(entry['eol']),
                extended_eol_date=(
                    # Special handling for Debian LTS releases.
                    datetime.datetime.fromtimestamp(LTS_RELEASES[entry['series']]).date()
                    if distributor_id == 'debian' and entry['series'] in LTS_RELEASES
                    # Ubuntu LTS releases are defined by the CSV file.
                    else parse_date(entry.get('eol-server'))
                ),
                release_date=parse_date(entry['release']),
                series=entry['series'],
                version=parse_version(entry['version']) if entry['version'] else None,
            )


def parse_date(value):
    """Convert a ``YYYY-MM-DD`` string to a :class:`datetime.date` object."""
    return datetime.datetime.strptime(value, '%Y-%m-%d').date() if value else None


def parse_version(value):
    """Convert a version string to a floating point number."""
    for token in value.split():
        try:
            return decimal.Decimal(token)
        except ValueError:
            pass
    msg = "Failed to convert version string to number! (%r)"
    raise ValueError(msg % value)


def ubuntu_keyring_updated():
    """
    Detect update `#1363482`_ to the ``ubuntu-keyring`` package.

    :returns: :data:`True` when version ``2016.10.27`` or newer is installed,
              :data:`False` when an older version is installed.

    This function checks if the changes discussed in Launchpad bug `#1363482`_
    apply to the current system using the ``dpkg-query --show`` and ``dpkg
    --compare-versions`` commands. For more details refer to `issue #8`_.

    .. _#1363482: https://bugs.launchpad.net/ubuntu/+source/ubuntu-keyring/+bug/1363482
    .. _issue #8: https://github.com/xolox/python-apt-mirror-updater/issues/8
    """
    try:
        # Try to return the cached value.
        return ubuntu_keyring_updated.cached_result
    except AttributeError:
        # Use external commands to check the installed version of the package.
        version = execute('dpkg-query', '--show', '--showformat=${Version}', 'ubuntu-keyring', capture=True)
        logger.debug("Detected ubuntu-keyring package version: %s", version)
        result = execute('dpkg', '--compare-versions', version, '>=', '2016.10.27', check=False, silent=True)
        logger.debug("Does Launchpad bug #1363482 apply? %s", result)
        # Cache the resulting value.
        ubuntu_keyring_updated.cached_result = result
        return result


class Release(PropertyManager):

    """Data class for metadata on Debian and Ubuntu releases."""

    @key_property
    def codename(self):
        """The long version of :attr:`series` (a string)."""

    @required_property
    def created_date(self):
        """The date on which the release was created (a :class:`~datetime.date` object)."""

    @key_property
    def distributor_id(self):
        """The name of the distributor (a string like ``debian`` or ``ubuntu``)."""

    @writable_property
    def eol_date(self):
        """The date on which the desktop release stops being supported (a :class:`~datetime.date` object)."""

    @writable_property
    def extended_eol_date(self):
        """The date on which the server release stops being supported (a :class:`~datetime.date` object)."""

    @lazy_property
    def is_eol(self):
        """Whether the release has reached its end-of-life date (a boolean or :data:`None`)."""
        eol_date = self.extended_eol_date or self.eol_date
        if eol_date:
            return datetime.date.today() >= eol_date
        else:
            return False

    @writable_property
    def is_lts(self):
        """Whether a release is a long term support release (a boolean)."""

    @writable_property
    def release_date(self):
        """The date on which the release was published (a :class:`~datetime.date` object)."""

    @key_property
    def series(self):
        """The short version of :attr:`codename` (a string)."""

    @writable_property
    def version(self):
        """
        The version number of the release (a :class:`~decimal.Decimal` number).

        This property has a :class:`~decimal.Decimal` value to enable proper
        sorting based on numeric comparison.
        """

    @lazy_property
    def keyring_file(self):
        """
        The pathname of the keyring with signing keys for this release (a string).

        This property exists to work around a bug in ``debootstrap`` which may
        use the wrong keyring to create Ubuntu chroots, for more details refer
        to :func:`ubuntu_keyring_updated()`.
        """
        filename = None
        reason = None
        logger.debug("Selecting keyring file for %s ..", self)
        if self.distributor_id == 'debian':
            filename = DEBIAN_KEYRING_CURRENT
            reason = "only known keyring"
        elif self.distributor_id == 'ubuntu':
            if ubuntu_keyring_updated():
                if self.version > decimal.Decimal('12.04'):
                    filename = UBUNTU_KEYRING_CURRENT
                    reason = "new keyring package / new release"
                else:
                    filename = UBUNTU_KEYRING_REMOVED
                    reason = "new keyring package / old release"
            else:
                filename = UBUNTU_KEYRING_CURRENT
                reason = "old keyring package"
        else:
            msg = "Unsupported distributor ID! (%s)"
            raise EnvironmentError(msg % self.distributor_id)
        logger.debug("Using %s (reason: %s).", filename, reason)
        return filename

    def __str__(self):
        """
        Render a human friendly representation of a :class:`Release` object.

        The result will be something like this:

        - Debian 9 (stretch)
        - Ubuntu 18.04 (bionic)
        """
        label = [self.distributor_id.capitalize()]
        if self.version:
            label.append(str(self.version))
        label.append("(%s)" % self.series)
        return " ".join(label)


# [[[cog
#
# import cog
# import decimal
# from apt_mirror_updater.releases import discover_releases
#
# indent = " " * 4
# cog.out("\nBUNDLED_RELEASES = [\n")
# for release in discover_releases():
#     cog.out(indent + "Release(\n")
#     for name in release.find_properties(cached=False):
#         value = getattr(release, name)
#         if value is not None:
#             if isinstance(value, decimal.Decimal):
#                 # It seems weirdly inconsistency to me that this is needed
#                 # for decimal.Decimal() but not for datetime.date() but I
#                 # guess the simple explanation is that repr() output simply
#                 # isn't guaranteed to be accepted by eval().
#                 value = "decimal." + repr(value)
#             else:
#                 value = repr(value)
#             cog.out(indent * 2 + name + "=" + value + ",\n")
#     cog.out(indent + "),\n")
# cog.out("]\n\n")
#
# ]]]

BUNDLED_RELEASES = [
    Release(
        codename='Experimental',
        created_date=datetime.date(1993, 8, 16),
        distributor_id='debian',
        is_lts=False,
        series='experimental',
    ),
    Release(
        codename='Sid',
        created_date=datetime.date(1993, 8, 16),
        distributor_id='debian',
        is_lts=False,
        series='sid',
    ),
    Release(
        codename='Buzz',
        created_date=datetime.date(1993, 8, 16),
        distributor_id='debian',
        eol_date=datetime.date(1997, 6, 5),
        is_lts=False,
        release_date=datetime.date(1996, 6, 17),
        series='buzz',
        version=decimal.Decimal('1.1'),
    ),
    Release(
        codename='Rex',
        created_date=datetime.date(1996, 6, 17),
        distributor_id='debian',
        eol_date=datetime.date(1998, 6, 5),
        is_lts=False,
        release_date=datetime.date(1996, 12, 12),
        series='rex',
        version=decimal.Decimal('1.2'),
    ),
    Release(
        codename='Bo',
        created_date=datetime.date(1996, 12, 12),
        distributor_id='debian',
        eol_date=datetime.date(1999, 3, 9),
        is_lts=False,
        release_date=datetime.date(1997, 6, 5),
        series='bo',
        version=decimal.Decimal('1.3'),
    ),
    Release(
        codename='Hamm',
        created_date=datetime.date(1997, 6, 5),
        distributor_id='debian',
        eol_date=datetime.date(2000, 3, 9),
        is_lts=False,
        release_date=datetime.date(1998, 7, 24),
        series='hamm',
        version=decimal.Decimal('2.0'),
    ),
    Release(
        codename='Slink',
        created_date=datetime.date(1998, 7, 24),
        distributor_id='debian',
        eol_date=datetime.date(2000, 10, 30),
        is_lts=False,
        release_date=datetime.date(1999, 3, 9),
        series='slink',
        version=decimal.Decimal('2.1'),
    ),
    Release(
        codename='Potato',
        created_date=datetime.date(1999, 3, 9),
        distributor_id='debian',
        eol_date=datetime.date(2003, 7, 30),
        is_lts=False,
        release_date=datetime.date(2000, 8, 15),
        series='potato',
        version=decimal.Decimal('2.2'),
    ),
    Release(
        codename='Woody',
        created_date=datetime.date(2000, 8, 15),
        distributor_id='debian',
        eol_date=datetime.date(2006, 6, 30),
        is_lts=False,
        release_date=datetime.date(2002, 7, 19),
        series='woody',
        version=decimal.Decimal('3.0'),
    ),
    Release(
        codename='Sarge',
        created_date=datetime.date(2002, 7, 19),
        distributor_id='debian',
        eol_date=datetime.date(2008, 3, 30),
        is_lts=False,
        release_date=datetime.date(2005, 6, 6),
        series='sarge',
        version=decimal.Decimal('3.1'),
    ),
    Release(
        codename='Etch',
        created_date=datetime.date(2005, 6, 6),
        distributor_id='debian',
        eol_date=datetime.date(2010, 2, 15),
        is_lts=False,
        release_date=datetime.date(2007, 4, 8),
        series='etch',
        version=decimal.Decimal('4.0'),
    ),
    Release(
        codename='Lenny',
        created_date=datetime.date(2007, 4, 8),
        distributor_id='debian',
        eol_date=datetime.date(2012, 2, 6),
        is_lts=False,
        release_date=datetime.date(2009, 2, 14),
        series='lenny',
        version=decimal.Decimal('5.0'),
    ),
    Release(
        codename='Squeeze',
        created_date=datetime.date(2009, 2, 14),
        distributor_id='debian',
        eol_date=datetime.date(2014, 5, 31),
        is_lts=False,
        release_date=datetime.date(2011, 2, 6),
        series='squeeze',
        version=decimal.Decimal('6.0'),
    ),
    Release(
        codename='Wheezy',
        created_date=datetime.date(2011, 2, 6),
        distributor_id='debian',
        eol_date=datetime.date(2016, 4, 26),
        is_lts=False,
        release_date=datetime.date(2013, 5, 4),
        series='wheezy',
        version=decimal.Decimal('7'),
    ),
    Release(
        codename='Jessie',
        created_date=datetime.date(2013, 5, 4),
        distributor_id='debian',
        eol_date=datetime.date(2018, 6, 6),
        is_lts=False,
        release_date=datetime.date(2015, 4, 25),
        series='jessie',
        version=decimal.Decimal('8'),
    ),
    Release(
        codename='Stretch',
        created_date=datetime.date(2015, 4, 25),
        distributor_id='debian',
        is_lts=False,
        release_date=datetime.date(2017, 6, 17),
        series='stretch',
        version=decimal.Decimal('9'),
    ),
    Release(
        codename='Buster',
        created_date=datetime.date(2017, 6, 17),
        distributor_id='debian',
        is_lts=False,
        series='buster',
        version=decimal.Decimal('10'),
    ),
    Release(
        codename='Bullseye',
        created_date=datetime.date(2019, 8, 1),
        distributor_id='debian',
        is_lts=False,
        series='bullseye',
        version=decimal.Decimal('11'),
    ),
    Release(
        codename='Bookworm',
        created_date=datetime.date(2021, 8, 1),
        distributor_id='debian',
        is_lts=False,
        series='bookworm',
        version=decimal.Decimal('12'),
    ),
    Release(
        codename='Warty Warthog',
        created_date=datetime.date(2004, 3, 5),
        distributor_id='ubuntu',
        eol_date=datetime.date(2006, 4, 30),
        is_lts=False,
        release_date=datetime.date(2004, 10, 20),
        series='warty',
        version=decimal.Decimal('4.10'),
    ),
    Release(
        codename='Hoary Hedgehog',
        created_date=datetime.date(2004, 10, 20),
        distributor_id='ubuntu',
        eol_date=datetime.date(2006, 10, 31),
        is_lts=False,
        release_date=datetime.date(2005, 4, 8),
        series='hoary',
        version=decimal.Decimal('5.04'),
    ),
    Release(
        codename='Breezy Badger',
        created_date=datetime.date(2005, 4, 8),
        distributor_id='ubuntu',
        eol_date=datetime.date(2007, 4, 13),
        is_lts=False,
        release_date=datetime.date(2005, 10, 12),
        series='breezy',
        version=decimal.Decimal('5.10'),
    ),
    Release(
        codename='Dapper Drake',
        created_date=datetime.date(2005, 10, 12),
        distributor_id='ubuntu',
        eol_date=datetime.date(2009, 7, 14),
        extended_eol_date=datetime.date(2011, 6, 1),
        is_lts=True,
        release_date=datetime.date(2006, 6, 1),
        series='dapper',
        version=decimal.Decimal('6.06'),
    ),
    Release(
        codename='Edgy Eft',
        created_date=datetime.date(2006, 6, 1),
        distributor_id='ubuntu',
        eol_date=datetime.date(2008, 4, 25),
        is_lts=False,
        release_date=datetime.date(2006, 10, 26),
        series='edgy',
        version=decimal.Decimal('6.10'),
    ),
    Release(
        codename='Feisty Fawn',
        created_date=datetime.date(2006, 10, 26),
        distributor_id='ubuntu',
        eol_date=datetime.date(2008, 10, 19),
        is_lts=False,
        release_date=datetime.date(2007, 4, 19),
        series='feisty',
        version=decimal.Decimal('7.04'),
    ),
    Release(
        codename='Gutsy Gibbon',
        created_date=datetime.date(2007, 4, 19),
        distributor_id='ubuntu',
        eol_date=datetime.date(2009, 4, 18),
        is_lts=False,
        release_date=datetime.date(2007, 10, 18),
        series='gutsy',
        version=decimal.Decimal('7.10'),
    ),
    Release(
        codename='Hardy Heron',
        created_date=datetime.date(2007, 10, 18),
        distributor_id='ubuntu',
        eol_date=datetime.date(2011, 5, 12),
        extended_eol_date=datetime.date(2013, 5, 9),
        is_lts=True,
        release_date=datetime.date(2008, 4, 24),
        series='hardy',
        version=decimal.Decimal('8.04'),
    ),
    Release(
        codename='Intrepid Ibex',
        created_date=datetime.date(2008, 4, 24),
        distributor_id='ubuntu',
        eol_date=datetime.date(2010, 4, 30),
        is_lts=False,
        release_date=datetime.date(2008, 10, 30),
        series='intrepid',
        version=decimal.Decimal('8.10'),
    ),
    Release(
        codename='Jaunty Jackalope',
        created_date=datetime.date(2008, 10, 30),
        distributor_id='ubuntu',
        eol_date=datetime.date(2010, 10, 23),
        is_lts=False,
        release_date=datetime.date(2009, 4, 23),
        series='jaunty',
        version=decimal.Decimal('9.04'),
    ),
    Release(
        codename='Karmic Koala',
        created_date=datetime.date(2009, 4, 23),
        distributor_id='ubuntu',
        eol_date=datetime.date(2011, 4, 29),
        is_lts=False,
        release_date=datetime.date(2009, 10, 29),
        series='karmic',
        version=decimal.Decimal('9.10'),
    ),
    Release(
        codename='Lucid Lynx',
        created_date=datetime.date(2009, 10, 29),
        distributor_id='ubuntu',
        eol_date=datetime.date(2013, 5, 9),
        extended_eol_date=datetime.date(2015, 4, 29),
        is_lts=True,
        release_date=datetime.date(2010, 4, 29),
        series='lucid',
        version=decimal.Decimal('10.04'),
    ),
    Release(
        codename='Maverick Meerkat',
        created_date=datetime.date(2010, 4, 29),
        distributor_id='ubuntu',
        eol_date=datetime.date(2012, 4, 10),
        is_lts=False,
        release_date=datetime.date(2010, 10, 10),
        series='maverick',
        version=decimal.Decimal('10.10'),
    ),
    Release(
        codename='Natty Narwhal',
        created_date=datetime.date(2010, 10, 10),
        distributor_id='ubuntu',
        eol_date=datetime.date(2012, 10, 28),
        is_lts=False,
        release_date=datetime.date(2011, 4, 28),
        series='natty',
        version=decimal.Decimal('11.04'),
    ),
    Release(
        codename='Oneiric Ocelot',
        created_date=datetime.date(2011, 4, 28),
        distributor_id='ubuntu',
        eol_date=datetime.date(2013, 5, 9),
        is_lts=False,
        release_date=datetime.date(2011, 10, 13),
        series='oneiric',
        version=decimal.Decimal('11.10'),
    ),
    Release(
        codename='Precise Pangolin',
        created_date=datetime.date(2011, 10, 13),
        distributor_id='ubuntu',
        eol_date=datetime.date(2017, 4, 26),
        is_lts=True,
        release_date=datetime.date(2012, 4, 26),
        series='precise',
        version=decimal.Decimal('12.04'),
    ),
    Release(
        codename='Quantal Quetzal',
        created_date=datetime.date(2012, 4, 26),
        distributor_id='ubuntu',
        eol_date=datetime.date(2014, 5, 16),
        is_lts=False,
        release_date=datetime.date(2012, 10, 18),
        series='quantal',
        version=decimal.Decimal('12.10'),
    ),
    Release(
        codename='Raring Ringtail',
        created_date=datetime.date(2012, 10, 18),
        distributor_id='ubuntu',
        eol_date=datetime.date(2014, 1, 27),
        is_lts=False,
        release_date=datetime.date(2013, 4, 25),
        series='raring',
        version=decimal.Decimal('13.04'),
    ),
    Release(
        codename='Saucy Salamander',
        created_date=datetime.date(2013, 4, 25),
        distributor_id='ubuntu',
        eol_date=datetime.date(2014, 7, 17),
        is_lts=False,
        release_date=datetime.date(2013, 10, 17),
        series='saucy',
        version=decimal.Decimal('13.10'),
    ),
    Release(
        codename='Trusty Tahr',
        created_date=datetime.date(2013, 10, 17),
        distributor_id='ubuntu',
        eol_date=datetime.date(2019, 4, 17),
        is_lts=True,
        release_date=datetime.date(2014, 4, 17),
        series='trusty',
        version=decimal.Decimal('14.04'),
    ),
    Release(
        codename='Utopic Unicorn',
        created_date=datetime.date(2014, 4, 17),
        distributor_id='ubuntu',
        eol_date=datetime.date(2015, 7, 23),
        is_lts=False,
        release_date=datetime.date(2014, 10, 23),
        series='utopic',
        version=decimal.Decimal('14.10'),
    ),
    Release(
        codename='Vivid Vervet',
        created_date=datetime.date(2014, 10, 23),
        distributor_id='ubuntu',
        eol_date=datetime.date(2016, 1, 23),
        is_lts=False,
        release_date=datetime.date(2015, 4, 23),
        series='vivid',
        version=decimal.Decimal('15.04'),
    ),
    Release(
        codename='Wily Werewolf',
        created_date=datetime.date(2015, 4, 23),
        distributor_id='ubuntu',
        eol_date=datetime.date(2016, 7, 22),
        is_lts=False,
        release_date=datetime.date(2015, 10, 22),
        series='wily',
        version=decimal.Decimal('15.10'),
    ),
    Release(
        codename='Xenial Xerus',
        created_date=datetime.date(2015, 10, 22),
        distributor_id='ubuntu',
        eol_date=datetime.date(2021, 4, 21),
        is_lts=True,
        release_date=datetime.date(2016, 4, 21),
        series='xenial',
        version=decimal.Decimal('16.04'),
    ),
    Release(
        codename='Yakkety Yak',
        created_date=datetime.date(2016, 4, 21),
        distributor_id='ubuntu',
        eol_date=datetime.date(2017, 7, 20),
        is_lts=False,
        release_date=datetime.date(2016, 10, 13),
        series='yakkety',
        version=decimal.Decimal('16.10'),
    ),
    Release(
        codename='Zesty Zapus',
        created_date=datetime.date(2016, 10, 13),
        distributor_id='ubuntu',
        eol_date=datetime.date(2018, 1, 13),
        is_lts=False,
        release_date=datetime.date(2017, 4, 13),
        series='zesty',
        version=decimal.Decimal('17.04'),
    ),
    Release(
        codename='Artful Aardvark',
        created_date=datetime.date(2017, 4, 13),
        distributor_id='ubuntu',
        eol_date=datetime.date(2018, 7, 19),
        is_lts=False,
        release_date=datetime.date(2017, 10, 19),
        series='artful',
        version=decimal.Decimal('17.10'),
    ),
    Release(
        codename='Bionic Beaver',
        created_date=datetime.date(2017, 10, 19),
        distributor_id='ubuntu',
        eol_date=datetime.date(2023, 4, 26),
        is_lts=True,
        release_date=datetime.date(2018, 4, 26),
        series='bionic',
        version=decimal.Decimal('18.04'),
    ),
    Release(
        codename='Cosmic Cuttlefish',
        created_date=datetime.date(2018, 4, 26),
        distributor_id='ubuntu',
        eol_date=datetime.date(2019, 7, 18),
        is_lts=False,
        release_date=datetime.date(2018, 10, 18),
        series='cosmic',
        version=decimal.Decimal('18.10'),
    ),
]

# [[[end]]]
