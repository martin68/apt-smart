#!/bin/bash -e

# This shell script is used by the makefile and Travis CI to run the
# apt-mirror-updater test suite as root, allowing it to make changes
# to the system that's running the test suite (one of my laptops
# or a Travis CI worker).

# Run the test suite with root privileges.
sudo $(which py.test) --cov

# Restore the ownership of the coverage data.
sudo chown --reference="$PWD" --recursive $PWD

# Update the HTML coverage overview.
coverage html
