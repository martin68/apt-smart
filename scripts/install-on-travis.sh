#!/bin/bash -e

# Install the required Python packages.
pip install --requirement=requirements-travis.txt

# For github actions python 3.12, install within virtualenv.
/home/runner/.virtualenvs/apt-smart/bin/pip install --upgrade pip setuptools wheel

# Install the project itself, making sure that potential character encoding
# and/or decoding errors in the setup script are caught as soon as possible.
LC_ALL=C pip install .

# Let apt-get, dpkg and related tools know that we want the following
# commands to be 100% automated (no interactive prompts).
export DEBIAN_FRONTEND=noninteractive

# Update apt-get's package lists.
sudo -E apt-get update -qq

# Make sure the /usr/share/distro-info/*.csv files are available,
# this enables the test_gather_eol_dates() test.
sudo -E apt-get install --yes distro-info-data
