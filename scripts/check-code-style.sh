#!/bin/bash -e

# I value automated code style checks that break my Travis CI builds but I also
# value compatibility with Python 2.6, however recently it seems that flake8
# has dropped Python 2.6 compatibility. That's only fair, but now I need to
# work around it, hence this trivial script :-).
# Python 2.7 also has an issue with flake8 now, so disable it for Python 2.7

if python -c 'import sys; sys.exit(0 if sys.version_info[:2] > (2, 7) else 1)'; then
  echo "Updating installation of flake8 .." >&2
  pip install --upgrade --quiet --requirement=requirements-checks.txt
  flake8
else
  echo "Skipping code style checks on Python 2.7 .." >&2
fi
