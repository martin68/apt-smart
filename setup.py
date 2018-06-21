#!/usr/bin/env python

# Setup script for the `apt-mirror-updater' package.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 22, 2018
# URL: https://apt-mirror-updater.readthedocs.io

"""
Setup script for the `apt-mirror-updater` package.

**python setup.py install**
  Install from the working directory into the current Python environment.

**python setup.py sdist**
  Build a source distribution archive.

**python setup.py bdist_wheel**
  Build a wheel distribution archive.
"""

# Standard library modules.
import codecs
import os
import re
import sys

# De-facto standard solution for Python packaging.
from setuptools import find_packages, setup


def get_contents(*args):
    """Get the contents of a file relative to the source distribution directory."""
    with codecs.open(get_absolute_path(*args), 'r', 'UTF-8') as handle:
        return handle.read()


def get_version(*args):
    """Extract the version number from a Python module."""
    contents = get_contents(*args)
    metadata = dict(re.findall('__([a-z]+)__ = [\'"]([^\'"]+)', contents))
    return metadata['version']


def get_install_requires():
    """Get the conditional dependencies for source distributions."""
    install_requires = get_requirements('requirements.txt')
    if 'bdist_wheel' not in sys.argv:
        if sys.version_info[:2] == (2, 6):
            # flufl.enum 4.1 drops Python 2.6 compatibility.
            install_requires.append('flufl.enum >= 4.0.1, < 4.1')
        elif sys.version_info[:2] < (3, 4):
            install_requires.append('flufl.enum >= 4.0.1')
    return sorted(install_requires)


def get_extras_require():
    """Get the conditional dependencies for wheel distributions."""
    extras_require = {}
    if have_environment_marker_support():
        # flufl.enum 4.1 drops Python 2.6 compatibility.
        extras_require[':python_version == "2.6"'] = ['flufl.enum >= 4.0.1, < 4.1']
        expression = ':%s' % ' or '.join([
            'python_version == "2.6"',
            'python_version == "2.7"',
            'python_version == "3.0"',
            'python_version == "3.1"',
            'python_version == "3.2"',
            'python_version == "3.3"',
        ])
        extras_require[expression] = ['flufl.enum >= 4.0.1']
    return extras_require


def get_requirements(*args):
    """Get requirements from pip requirement files."""
    requirements = set()
    with open(get_absolute_path(*args)) as handle:
        for line in handle:
            # Strip comments.
            line = re.sub(r'^#.*|\s#.*', '', line)
            # Ignore empty lines
            if line and not line.isspace():
                requirements.add(re.sub(r'\s+', '', line))
    return sorted(requirements)


def get_absolute_path(*args):
    """Transform relative pathnames into absolute pathnames."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *args)


def have_environment_marker_support():
    """
    Check whether setuptools has support for PEP-426 environment marker support.

    Based on the ``setup.py`` script of the ``pytest`` package:
    https://bitbucket.org/pytest-dev/pytest/src/default/setup.py
    """
    try:
        from pkg_resources import parse_version
        from setuptools import __version__
        return parse_version(__version__) >= parse_version('0.7.2')
    except Exception:
        return False


setup(
    name='apt-mirror-updater',
    version=get_version('apt_mirror_updater', '__init__.py'),
    description="Automated, robust apt-get mirror selection for Debian and Ubuntu",
    long_description=get_contents('README.rst'),
    url='https://apt-mirror-updater.readthedocs.io',
    author='Peter Odding',
    author_email='peter@peterodding.com',
    license='MIT',
    packages=find_packages(),
    install_requires=get_install_requires(),
    extras_require=get_extras_require(),
    entry_points=dict(console_scripts=[
        'apt-mirror-updater = apt_mirror_updater.cli:main',
    ]),
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',
        'Intended Audience :: System Administrators',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Operating System :: POSIX :: Linux',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Topic :: Software Development',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Shells',
        'Topic :: System :: System Shells',
        'Topic :: System :: Systems Administration',
        'Topic :: Terminals',
        'Topic :: Utilities',
    ])
