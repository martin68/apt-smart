# Easy to use metadata on Debian and Ubuntu releases.
#
# Author: martin68 and Peter Odding
# Last Change: May 31, 2020
# URL: https://apt-smart.readthedocs.io

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
available in Python. This enables `apt-smart` to make an informed
decision about the following questions:

1. Is a given Debian or Ubuntu release expected to be available on mirrors or
   will it only be available in the archive of old releases?

2. Is the signing key of a given Ubuntu release expected to be included in the
   main keyring (:data:`UBUNTU_KEYRING_CURRENT`) or should the keyring with
   removed keys (:data:`UBUNTU_KEYRING_REMOVED`) be used?

To make it possible to run `apt-smart` without direct access to the
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
import six
from executor import execute
from humanfriendly.decorators import cached
try:
    from property_manager3 import PropertyManager, key_property, lazy_property, required_property, writable_property
except ImportError:
    from property_manager import PropertyManager, key_property, lazy_property, required_property, writable_property
from six import string_types
from itertools import product


DISTRO_INFO_DIRECTORY = '/usr/share/distro-info'
"""The pathname of the directory with CSV files containing release metadata (a string)."""

DEBIAN_KEYRING_CURRENT = '/usr/share/keyrings/debian-archive-keyring.gpg'
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
            msg = "The number %s doesn't match a known Debian or Ubuntu or Linux Mint release!"
            raise ValueError(msg % value)
        return matches[0]
    # Other strings are matched against release code names.
    matches = [release for release in discover_releases() if value.lower() in release.codename.lower()]
    if len(matches) != 1:
        msg = "The string %r doesn't match a known Debian or Ubuntu or Linux Mint release!"
        raise ValueError(msg % value)
    return matches[0]


@cached
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
    # Discover the known releases on the first call to discover_releases().
    # First we check the CSV files on the system where apt-smart
    # is running, because those files may be more up-to-date than the
    # bundled information is.
    result = set()
    for filename in glob.glob(os.path.join(DISTRO_INFO_DIRECTORY, '*.csv')):
        for release in parse_csv_file(filename):
            result.add(release)
    # Add the releases bundled with apt-smart to the result
    # without causing duplicate entries (due to the use of a set and key
    # properties).
    result.update(BUNDLED_RELEASES)
    # Sort the releases by distributor ID and version / series.
    return sorted(result, key=lambda r: (r.distributor_id, r.version or 0, r.series))


def table_to_2d(table_tag):  # https://stackoverflow.com/a/48451104
    rowspans = []  # track pending rowspans
    rows = table_tag.find_all('tr')

    # first scan, see how many columns we need
    colcount = 0
    for r, row in enumerate(rows):
        cells = row.find_all(['td', 'th'], recursive=False)
        # count columns (including spanned).
        # add active rowspans from preceding rows
        # we *ignore* the colspan value on the last cell, to prevent
        # creating 'phantom' columns with no actual cells, only extended
        # colspans. This is achieved by hardcoding the last cell width as 1.
        # a colspan of 0 means "fill until the end" but can really only apply
        # to the last cell; ignore it elsewhere.
        colcount = max(
            colcount,
            sum(int(c.get('colspan', 1)) or 1 for c in cells[:-1]) + len(cells[-1:]) + len(rowspans))
        # update rowspan bookkeeping; 0 is a span to the bottom.
        rowspans += [int(c.get('rowspan', 1)) or len(rows) - r for c in cells]
        rowspans = [s - 1 for s in rowspans if s > 1]

    # it doesn't matter if there are still rowspan numbers 'active'; no extra
    # rows to show in the table means the larger than 1 rowspan numbers in the
    # last table row are ignored.

    # build an empty matrix for all possible cells
    table = [[None] * colcount for row in rows]

    # fill matrix from row data
    rowspans = {}  # track pending rowspans, column number mapping to count
    for row, row_elem in enumerate(rows):
        span_offset = 0  # how many columns are skipped due to row and colspans
        for col, cell in enumerate(row_elem.find_all(['td', 'th'], recursive=False)):
            # adjust for preceding row and colspans
            col += span_offset
            while rowspans.get(col, 0):
                span_offset += 1
                col += 1

            # fill table data
            rowspan = rowspans[col] = int(cell.get('rowspan', 1)) or len(rows) - row
            colspan = int(cell.get('colspan', 1)) or colcount - col
            # next column is offset by the colspan
            span_offset += colspan - 1
            value = cell.get_text()
            for drow, dcol in product(range(rowspan), range(colspan)):
                try:
                    table[row + drow][col + dcol] = value
                    rowspans[col + dcol] = rowspan
                except IndexError:
                    # rowspan or colspan outside the confines of the table
                    pass

        # update rowspan bookkeeping
        rowspans = {c: s - 1 for c, s in rowspans.items() if s > 1}

    return table


def discover_linuxmint_releases(array_2d):
    d = {}  # a dict to map table head to column number
    head = array_2d[0]
    for i, data in enumerate(head):
        d[data] = i
    last = None
    for entry in array_2d[1:-1]:
        if entry[d['Codename\n']] == last:
            continue  # skip same codename entry
        last = entry[d['Codename\n']]

        yield Release(
            codename=parse_data_wiki(entry[d['Codename\n']]),
            compatible_repository=(
                parse_data_wiki(entry[d['Compatible repository\n']]).split('(')[1].split(' ')[0].lower()
                if entry[d['Compatible repository\n']].find('(') > 0
                else parse_data_wiki(entry[d['Compatible repository\n']])
            ),
            is_lts=('Yes' in entry[d['LTS?\n']]),
            created_date=parse_date_wiki(entry[d['Release date\n']]),
            distributor_id='linuxmint',
            eol_date=parse_date_wiki(entry[d['Support End\n']]),
            extended_eol_date=parse_date_wiki(entry[d['Support End\n']]),
            release_date=parse_date_wiki(entry[d['Release date\n']]),
            series=parse_data_wiki(entry[d['Codename\n']]).lower(),
            version=parse_version_wiki(entry[d['Version\n']]) if entry[d['Version\n']] else None,
        )


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
    from apt_smart.backends.debian import LTS_RELEASES
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
                        )
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


def parse_data_wiki(value):
    r"""Strip a string such as ``19 December 2018 [18]\n`` to ``19 December 2018``"""
    if six.PY2:
        return value.encode("utf8").split('[')[0].strip()
    else:
        return value.split('[')[0].strip()


def parse_date(value):
    """Convert a ``YYYY-MM-DD`` string to a :class:`datetime.date` object."""
    return datetime.datetime.strptime(value, '%Y-%m-%d').date() if value else None


def parse_date_wiki(value):
    r"""Convert a string such as ``19 December 2018`` ``August 02, 2019\n`` to a :class:`datetime.date` object."""
    value = parse_data_wiki(value)
    if value == 'Unknown':
        value = '30 April 2008'
    if len(value) < 15:
        if not value[:1].isdigit():  # Only Month Year
            value = '30 ' + value
        if len(value) < 5:  # Only Year
            value = '30 April ' + value
    try:
        return datetime.datetime.strptime(value, '%d %B %Y').date() if value else None
    except ValueError:
        return datetime.datetime.strptime(value, '%B %d, %Y').date() if value else None


def parse_version(value):
    """Convert a version string to a floating point number."""
    for token in value.split():
        try:
            return decimal.Decimal(token)
        except ValueError:
            pass
    msg = "Failed to convert version string to number! (%r)"
    raise ValueError(msg % value)


def parse_version_wiki(value):
    """Convert a version string (got from wiki page) to a floating point number."""
    value = parse_data_wiki(value)
    value = value.split(": ")[1]
    for token in value.split():
        try:
            return decimal.Decimal(token)
        except ValueError:
            pass
    msg = "Failed to convert version string to number! (%r)"
    raise ValueError(msg % value)


@cached
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
    # Use external commands to check the installed version of the package.
    version = execute('dpkg-query', '--show', '--showformat=${Version}', 'ubuntu-keyring', capture=True)
    logger.debug("Detected ubuntu-keyring package version: %s", version)
    updated = execute('dpkg', '--compare-versions', version, '>=', '2016.10.27', check=False, silent=True)
    logger.debug("Does Launchpad bug #1363482 apply? %s", updated)
    return updated


class Release(PropertyManager):

    """Data class for metadata on Debian and Ubuntu releases."""

    @key_property
    def codename(self):
        """The long version of :attr:`series` (a string)."""

    @writable_property
    def compatible_repository(self):
        """For Linux Mint, compatible which Ubuntu version's repository"""

    @required_property
    def created_date(self):
        """The date on which the release was created (a :class:`~datetime.date` object)."""

    @key_property
    def distributor_id(self):
        """The name of the distributor (a string like ``debian`` or ``ubuntu`` or ``linuxmint``)."""

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
# from bs4 import BeautifulSoup
# from apt_smart.releases import discover_releases, discover_linuxmint_releases, table_to_2d
# from apt_smart.http import fetch_url
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
# url = 'https://en.wikipedia.org/wiki/Linux_Mint_version_history'
# response = fetch_url(url, timeout=15, retry=True)
# soup = BeautifulSoup(response, 'html.parser')
# tables = soup.findAll('table')
# for release in discover_linuxmint_releases(table_to_2d(tables[1])):
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
        extended_eol_date=datetime.date(2020, 6, 30),
        is_lts=True,
        release_date=datetime.date(2015, 4, 25),
        series='jessie',
        version=decimal.Decimal('8'),
    ),
    Release(
        codename='Stretch',
        created_date=datetime.date(2015, 4, 25),
        distributor_id='debian',
        eol_date=datetime.date(2020, 7, 6),
        extended_eol_date=datetime.date(2022, 6, 30),
        is_lts=True,
        release_date=datetime.date(2017, 6, 17),
        series='stretch',
        version=decimal.Decimal('9'),
    ),
    Release(
        codename='Buster',
        created_date=datetime.date(2017, 6, 17),
        distributor_id='debian',
        is_lts=False,
        release_date=datetime.date(2019, 7, 6),
        series='buster',
        version=decimal.Decimal('10'),
    ),
    Release(
        codename='Bullseye',
        created_date=datetime.date(2019, 7, 6),
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
        codename='Ada',
        compatible_repository='Kubuntu 6.06',
        created_date=datetime.date(2006, 8, 27),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 8, 27),
        series='ada',
        version=decimal.Decimal('1.0'),
    ),
    Release(
        codename='Barbara',
        compatible_repository='edgy',
        created_date=datetime.date(2006, 11, 13),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 11, 13),
        series='barbara',
        version=decimal.Decimal('2.0'),
    ),
    Release(
        codename='Bea',
        compatible_repository='edgy',
        created_date=datetime.date(2006, 12, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 12, 20),
        series='bea',
        version=decimal.Decimal('2.1'),
    ),
    Release(
        codename='Bianca',
        compatible_repository='edgy',
        created_date=datetime.date(2007, 2, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2007, 2, 20),
        series='bianca',
        version=decimal.Decimal('2.2'),
    ),
    Release(
        codename='Cassandra',
        compatible_repository='feisty',
        created_date=datetime.date(2007, 5, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 10, 30),
        extended_eol_date=datetime.date(2008, 10, 30),
        is_lts=False,
        release_date=datetime.date(2007, 5, 30),
        series='cassandra',
        version=decimal.Decimal('3.0'),
    ),
    Release(
        codename='Celena',
        compatible_repository='feisty',
        created_date=datetime.date(2007, 9, 24),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 10, 30),
        extended_eol_date=datetime.date(2008, 10, 30),
        is_lts=False,
        release_date=datetime.date(2007, 9, 24),
        series='celena',
        version=decimal.Decimal('3.1'),
    ),
    Release(
        codename='Daryna',
        compatible_repository='gutsy',
        created_date=datetime.date(2007, 10, 15),
        distributor_id='linuxmint',
        eol_date=datetime.date(2009, 4, 30),
        extended_eol_date=datetime.date(2009, 4, 30),
        is_lts=False,
        release_date=datetime.date(2007, 10, 15),
        series='daryna',
        version=decimal.Decimal('4.0'),
    ),
    Release(
        codename='Elyssa',
        compatible_repository='hardy',
        created_date=datetime.date(2008, 6, 8),
        distributor_id='linuxmint',
        eol_date=datetime.date(2011, 4, 30),
        extended_eol_date=datetime.date(2011, 4, 30),
        is_lts=True,
        release_date=datetime.date(2008, 6, 8),
        series='elyssa',
        version=decimal.Decimal('5'),
    ),
    Release(
        codename='Felicia',
        compatible_repository='intrepid',
        created_date=datetime.date(2008, 12, 15),
        distributor_id='linuxmint',
        eol_date=datetime.date(2010, 4, 30),
        extended_eol_date=datetime.date(2010, 4, 30),
        is_lts=False,
        release_date=datetime.date(2008, 12, 15),
        series='felicia',
        version=decimal.Decimal('6'),
    ),
    Release(
        codename='Gloria',
        compatible_repository='jaunty',
        created_date=datetime.date(2009, 5, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2010, 10, 30),
        extended_eol_date=datetime.date(2010, 10, 30),
        is_lts=False,
        release_date=datetime.date(2009, 5, 26),
        series='gloria',
        version=decimal.Decimal('7'),
    ),
    Release(
        codename='Helena',
        compatible_repository='karmic',
        created_date=datetime.date(2009, 11, 28),
        distributor_id='linuxmint',
        eol_date=datetime.date(2011, 4, 30),
        extended_eol_date=datetime.date(2011, 4, 30),
        is_lts=False,
        release_date=datetime.date(2009, 11, 28),
        series='helena',
        version=decimal.Decimal('8'),
    ),
    Release(
        codename='Isadora',
        compatible_repository='lucid',
        created_date=datetime.date(2010, 5, 18),
        distributor_id='linuxmint',
        eol_date=datetime.date(2013, 4, 30),
        extended_eol_date=datetime.date(2013, 4, 30),
        is_lts=True,
        release_date=datetime.date(2010, 5, 18),
        series='isadora',
        version=decimal.Decimal('9'),
    ),
    Release(
        codename='Julia',
        compatible_repository='maverick',
        created_date=datetime.date(2010, 11, 12),
        distributor_id='linuxmint',
        eol_date=datetime.date(2012, 4, 30),
        extended_eol_date=datetime.date(2012, 4, 30),
        is_lts=False,
        release_date=datetime.date(2010, 11, 12),
        series='julia',
        version=decimal.Decimal('10'),
    ),
    Release(
        codename='Katya',
        compatible_repository='natty',
        created_date=datetime.date(2011, 5, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2012, 10, 30),
        extended_eol_date=datetime.date(2012, 10, 30),
        is_lts=False,
        release_date=datetime.date(2011, 5, 26),
        series='katya',
        version=decimal.Decimal('11'),
    ),
    Release(
        codename='Lisa',
        compatible_repository='oneiric',
        created_date=datetime.date(2011, 11, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2013, 4, 30),
        extended_eol_date=datetime.date(2013, 4, 30),
        is_lts=False,
        release_date=datetime.date(2011, 11, 26),
        series='lisa',
        version=decimal.Decimal('12'),
    ),
    Release(
        codename='Maya',
        compatible_repository='precise',
        created_date=datetime.date(2012, 5, 23),
        distributor_id='linuxmint',
        eol_date=datetime.date(2017, 4, 30),
        extended_eol_date=datetime.date(2017, 4, 30),
        is_lts=True,
        release_date=datetime.date(2012, 5, 23),
        series='maya',
        version=decimal.Decimal('13'),
    ),
    Release(
        codename='Nadia',
        compatible_repository='quantal',
        created_date=datetime.date(2012, 11, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 5, 30),
        extended_eol_date=datetime.date(2014, 5, 30),
        is_lts=False,
        release_date=datetime.date(2012, 11, 20),
        series='nadia',
        version=decimal.Decimal('14'),
    ),
    Release(
        codename='Olivia',
        compatible_repository='raring',
        created_date=datetime.date(2013, 5, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 1, 30),
        extended_eol_date=datetime.date(2014, 1, 30),
        is_lts=False,
        release_date=datetime.date(2013, 5, 29),
        series='olivia',
        version=decimal.Decimal('15'),
    ),
    Release(
        codename='Petra',
        compatible_repository='saucy',
        created_date=datetime.date(2013, 11, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 7, 30),
        extended_eol_date=datetime.date(2014, 7, 30),
        is_lts=False,
        release_date=datetime.date(2013, 11, 30),
        series='petra',
        version=decimal.Decimal('16'),
    ),
    Release(
        codename='Qiana',
        compatible_repository='trusty',
        created_date=datetime.date(2014, 5, 31),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2014, 5, 31),
        series='qiana',
        version=decimal.Decimal('17'),
    ),
    Release(
        codename='Rebecca',
        compatible_repository='trusty',
        created_date=datetime.date(2014, 11, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2014, 11, 29),
        series='rebecca',
        version=decimal.Decimal('17.1'),
    ),
    Release(
        codename='Rafaela',
        compatible_repository='trusty',
        created_date=datetime.date(2015, 6, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2015, 6, 30),
        series='rafaela',
        version=decimal.Decimal('17.2'),
    ),
    Release(
        codename='Rosa',
        compatible_repository='trusty',
        created_date=datetime.date(2015, 12, 4),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2015, 12, 4),
        series='rosa',
        version=decimal.Decimal('17.3'),
    ),
    Release(
        codename='Sarah',
        compatible_repository='xenial',
        created_date=datetime.date(2016, 6, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2016, 6, 30),
        series='sarah',
        version=decimal.Decimal('18'),
    ),
    Release(
        codename='Serena',
        compatible_repository='xenial',
        created_date=datetime.date(2016, 12, 16),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2016, 12, 16),
        series='serena',
        version=decimal.Decimal('18.1'),
    ),
    Release(
        codename='Sonya',
        compatible_repository='xenial',
        created_date=datetime.date(2017, 7, 2),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2017, 7, 2),
        series='sonya',
        version=decimal.Decimal('18.2'),
    ),
    Release(
        codename='Sylvia',
        compatible_repository='xenial',
        created_date=datetime.date(2017, 11, 27),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2017, 11, 27),
        series='sylvia',
        version=decimal.Decimal('18.3'),
    ),
    Release(
        codename='Tara',
        compatible_repository='bionic',
        created_date=datetime.date(2018, 6, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2018, 6, 29),
        series='tara',
        version=decimal.Decimal('19'),
    ),
    Release(
        codename='Tessa',
        compatible_repository='bionic',
        created_date=datetime.date(2018, 12, 19),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2018, 12, 19),
        series='tessa',
        version=decimal.Decimal('19.1'),
    ),
    Release(
        codename='Tina',
        compatible_repository='bionic',
        created_date=datetime.date(2019, 8, 2),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2019, 8, 2),
        series='tina',
        version=decimal.Decimal('19.2'),
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
        extended_eol_date=datetime.date(2017, 4, 26),
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
        eol_date=datetime.date(2019, 4, 25),
        extended_eol_date=datetime.date(2019, 4, 25),
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
        extended_eol_date=datetime.date(2021, 4, 21),
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
        extended_eol_date=datetime.date(2023, 4, 26),
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
    Release(
        codename='Disco Dingo',
        created_date=datetime.date(2018, 10, 18),
        distributor_id='ubuntu',
        eol_date=datetime.date(2020, 1, 18),
        is_lts=False,
        release_date=datetime.date(2019, 4, 18),
        series='disco',
        version=decimal.Decimal('19.04'),
    ),
    Release(
        codename='Eoan Ermine',
        created_date=datetime.date(2019, 4, 18),
        distributor_id='ubuntu',
        eol_date=datetime.date(2020, 7, 17),
        is_lts=False,
        release_date=datetime.date(2019, 10, 17),
        series='eoan',
        version=decimal.Decimal('19.10'),
    ),
    Release(
        codename='Focal Fossa',
        created_date=datetime.date(2019, 10, 17),
        distributor_id='ubuntu',
        eol_date=datetime.date(2025, 4, 23),
        extended_eol_date=datetime.date(2025, 4, 23),
        is_lts=True,
        release_date=datetime.date(2020, 4, 23),
        series='focal',
        version=decimal.Decimal('20.04'),
    ),
    Release(
        codename='Groovy Gorilla',
        created_date=datetime.date(2020, 4, 23),
        distributor_id='ubuntu',
        eol_date=datetime.date(2021, 7, 22),
        is_lts=False,
        release_date=datetime.date(2020, 10, 22),
        series='groovy',
        version=decimal.Decimal('20.10'),
    ),
    Release(
        codename='Ada',
        compatible_repository='Kubuntu 6.06',
        created_date=datetime.date(2006, 8, 27),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 8, 27),
        series='ada',
        version=decimal.Decimal('1.0'),
    ),
    Release(
        codename='Barbara',
        compatible_repository='edgy',
        created_date=datetime.date(2006, 11, 13),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 11, 13),
        series='barbara',
        version=decimal.Decimal('2.0'),
    ),
    Release(
        codename='Bea',
        compatible_repository='edgy',
        created_date=datetime.date(2006, 12, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2006, 12, 20),
        series='bea',
        version=decimal.Decimal('2.1'),
    ),
    Release(
        codename='Bianca',
        compatible_repository='edgy',
        created_date=datetime.date(2007, 2, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 4, 30),
        extended_eol_date=datetime.date(2008, 4, 30),
        is_lts=False,
        release_date=datetime.date(2007, 2, 20),
        series='bianca',
        version=decimal.Decimal('2.2'),
    ),
    Release(
        codename='Cassandra',
        compatible_repository='feisty',
        created_date=datetime.date(2007, 5, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 10, 30),
        extended_eol_date=datetime.date(2008, 10, 30),
        is_lts=False,
        release_date=datetime.date(2007, 5, 30),
        series='cassandra',
        version=decimal.Decimal('3.0'),
    ),
    Release(
        codename='Celena',
        compatible_repository='feisty',
        created_date=datetime.date(2007, 9, 24),
        distributor_id='linuxmint',
        eol_date=datetime.date(2008, 10, 30),
        extended_eol_date=datetime.date(2008, 10, 30),
        is_lts=False,
        release_date=datetime.date(2007, 9, 24),
        series='celena',
        version=decimal.Decimal('3.1'),
    ),
    Release(
        codename='Daryna',
        compatible_repository='gutsy',
        created_date=datetime.date(2007, 10, 15),
        distributor_id='linuxmint',
        eol_date=datetime.date(2009, 4, 30),
        extended_eol_date=datetime.date(2009, 4, 30),
        is_lts=False,
        release_date=datetime.date(2007, 10, 15),
        series='daryna',
        version=decimal.Decimal('4.0'),
    ),
    Release(
        codename='Elyssa',
        compatible_repository='hardy',
        created_date=datetime.date(2008, 6, 8),
        distributor_id='linuxmint',
        eol_date=datetime.date(2011, 4, 30),
        extended_eol_date=datetime.date(2011, 4, 30),
        is_lts=True,
        release_date=datetime.date(2008, 6, 8),
        series='elyssa',
        version=decimal.Decimal('5'),
    ),
    Release(
        codename='Felicia',
        compatible_repository='intrepid',
        created_date=datetime.date(2008, 12, 15),
        distributor_id='linuxmint',
        eol_date=datetime.date(2010, 4, 30),
        extended_eol_date=datetime.date(2010, 4, 30),
        is_lts=False,
        release_date=datetime.date(2008, 12, 15),
        series='felicia',
        version=decimal.Decimal('6'),
    ),
    Release(
        codename='Gloria',
        compatible_repository='jaunty',
        created_date=datetime.date(2009, 5, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2010, 10, 30),
        extended_eol_date=datetime.date(2010, 10, 30),
        is_lts=False,
        release_date=datetime.date(2009, 5, 26),
        series='gloria',
        version=decimal.Decimal('7'),
    ),
    Release(
        codename='Helena',
        compatible_repository='karmic',
        created_date=datetime.date(2009, 11, 28),
        distributor_id='linuxmint',
        eol_date=datetime.date(2011, 4, 30),
        extended_eol_date=datetime.date(2011, 4, 30),
        is_lts=False,
        release_date=datetime.date(2009, 11, 28),
        series='helena',
        version=decimal.Decimal('8'),
    ),
    Release(
        codename='Isadora',
        compatible_repository='lucid',
        created_date=datetime.date(2010, 5, 18),
        distributor_id='linuxmint',
        eol_date=datetime.date(2013, 4, 30),
        extended_eol_date=datetime.date(2013, 4, 30),
        is_lts=True,
        release_date=datetime.date(2010, 5, 18),
        series='isadora',
        version=decimal.Decimal('9'),
    ),
    Release(
        codename='Julia',
        compatible_repository='maverick',
        created_date=datetime.date(2010, 11, 12),
        distributor_id='linuxmint',
        eol_date=datetime.date(2012, 4, 30),
        extended_eol_date=datetime.date(2012, 4, 30),
        is_lts=False,
        release_date=datetime.date(2010, 11, 12),
        series='julia',
        version=decimal.Decimal('10'),
    ),
    Release(
        codename='Katya',
        compatible_repository='natty',
        created_date=datetime.date(2011, 5, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2012, 10, 30),
        extended_eol_date=datetime.date(2012, 10, 30),
        is_lts=False,
        release_date=datetime.date(2011, 5, 26),
        series='katya',
        version=decimal.Decimal('11'),
    ),
    Release(
        codename='Lisa',
        compatible_repository='oneiric',
        created_date=datetime.date(2011, 11, 26),
        distributor_id='linuxmint',
        eol_date=datetime.date(2013, 4, 30),
        extended_eol_date=datetime.date(2013, 4, 30),
        is_lts=False,
        release_date=datetime.date(2011, 11, 26),
        series='lisa',
        version=decimal.Decimal('12'),
    ),
    Release(
        codename='Maya',
        compatible_repository='precise',
        created_date=datetime.date(2012, 5, 23),
        distributor_id='linuxmint',
        eol_date=datetime.date(2017, 4, 30),
        extended_eol_date=datetime.date(2017, 4, 30),
        is_lts=True,
        release_date=datetime.date(2012, 5, 23),
        series='maya',
        version=decimal.Decimal('13'),
    ),
    Release(
        codename='Nadia',
        compatible_repository='quantal',
        created_date=datetime.date(2012, 11, 20),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 5, 30),
        extended_eol_date=datetime.date(2014, 5, 30),
        is_lts=False,
        release_date=datetime.date(2012, 11, 20),
        series='nadia',
        version=decimal.Decimal('14'),
    ),
    Release(
        codename='Olivia',
        compatible_repository='raring',
        created_date=datetime.date(2013, 5, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 1, 30),
        extended_eol_date=datetime.date(2014, 1, 30),
        is_lts=False,
        release_date=datetime.date(2013, 5, 29),
        series='olivia',
        version=decimal.Decimal('15'),
    ),
    Release(
        codename='Petra',
        compatible_repository='saucy',
        created_date=datetime.date(2013, 11, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2014, 7, 30),
        extended_eol_date=datetime.date(2014, 7, 30),
        is_lts=False,
        release_date=datetime.date(2013, 11, 30),
        series='petra',
        version=decimal.Decimal('16'),
    ),
    Release(
        codename='Qiana',
        compatible_repository='trusty',
        created_date=datetime.date(2014, 5, 31),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2014, 5, 31),
        series='qiana',
        version=decimal.Decimal('17'),
    ),
    Release(
        codename='Rebecca',
        compatible_repository='trusty',
        created_date=datetime.date(2014, 11, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2014, 11, 29),
        series='rebecca',
        version=decimal.Decimal('17.1'),
    ),
    Release(
        codename='Rafaela',
        compatible_repository='trusty',
        created_date=datetime.date(2015, 6, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2015, 6, 30),
        series='rafaela',
        version=decimal.Decimal('17.2'),
    ),
    Release(
        codename='Rosa',
        compatible_repository='trusty',
        created_date=datetime.date(2015, 12, 4),
        distributor_id='linuxmint',
        eol_date=datetime.date(2019, 4, 30),
        extended_eol_date=datetime.date(2019, 4, 30),
        is_lts=True,
        release_date=datetime.date(2015, 12, 4),
        series='rosa',
        version=decimal.Decimal('17.3'),
    ),
    Release(
        codename='Sarah',
        compatible_repository='xenial',
        created_date=datetime.date(2016, 6, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2016, 6, 30),
        series='sarah',
        version=decimal.Decimal('18'),
    ),
    Release(
        codename='Serena',
        compatible_repository='xenial',
        created_date=datetime.date(2016, 12, 16),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2016, 12, 16),
        series='serena',
        version=decimal.Decimal('18.1'),
    ),
    Release(
        codename='Sonya',
        compatible_repository='xenial',
        created_date=datetime.date(2017, 7, 2),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2017, 7, 2),
        series='sonya',
        version=decimal.Decimal('18.2'),
    ),
    Release(
        codename='Sylvia',
        compatible_repository='xenial',
        created_date=datetime.date(2017, 11, 27),
        distributor_id='linuxmint',
        eol_date=datetime.date(2021, 4, 30),
        extended_eol_date=datetime.date(2021, 4, 30),
        is_lts=True,
        release_date=datetime.date(2017, 11, 27),
        series='sylvia',
        version=decimal.Decimal('18.3'),
    ),
    Release(
        codename='Tara',
        compatible_repository='bionic',
        created_date=datetime.date(2018, 6, 29),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2018, 6, 29),
        series='tara',
        version=decimal.Decimal('19'),
    ),
    Release(
        codename='Tessa',
        compatible_repository='bionic',
        created_date=datetime.date(2018, 12, 19),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2018, 12, 19),
        series='tessa',
        version=decimal.Decimal('19.1'),
    ),
    Release(
        codename='Tina',
        compatible_repository='bionic',
        created_date=datetime.date(2019, 8, 2),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2019, 8, 2),
        series='tina',
        version=decimal.Decimal('19.2'),
    ),
    Release(
        codename='Tricia',
        compatible_repository='bionic',
        created_date=datetime.date(2019, 12, 18),
        distributor_id='linuxmint',
        eol_date=datetime.date(2023, 4, 30),
        extended_eol_date=datetime.date(2023, 4, 30),
        is_lts=True,
        release_date=datetime.date(2019, 12, 18),
        series='tricia',
        version=decimal.Decimal('19.3'),
    ),
    Release(
        codename='Ulyana',
        compatible_repository='focal',
        created_date=datetime.date(2020, 6, 30),
        distributor_id='linuxmint',
        eol_date=datetime.date(2025, 4, 30),
        extended_eol_date=datetime.date(2025, 4, 30),
        is_lts=True,
        release_date=datetime.date(2020, 6, 30),
        series='ulyana',
        version=decimal.Decimal('20'),
    ),
]

# [[[end]]]
