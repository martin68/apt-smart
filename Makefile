# Makefile for the 'apt-smart' package.
#
# Author: martin68 and Peter Odding
# Last Change: September 15, 2019
# URL: https://apt-smart.readthedocs.io

PACKAGE_NAME = apt-smart
WORKON_HOME ?= $(HOME)/.virtualenvs
VIRTUAL_ENV ?= $(WORKON_HOME)/$(PACKAGE_NAME)
PATH := $(VIRTUAL_ENV)/bin:$(PATH)
MAKE := $(MAKE) --no-print-directory
SHELL = bash

default:
	@echo "Makefile for $(PACKAGE_NAME)"
	@echo
	@echo 'Usage:'
	@echo
	@echo '    make install    install the package in a virtual environment'
	@echo '    make reset      recreate the virtual environment'
	@echo '    make check      check coding style (PEP-8, PEP-257)'
	@echo '    make test       run the test suite, report coverage'
	@echo '    make tox        run the tests on all Python versions'
	@echo '    make eol        update apt_smart.eol module'
	@echo '    make readme     update usage in readme'
	@echo '    make docs       update documentation using Sphinx'
	@echo '    make publish    publish changes to GitHub/PyPI'
	@echo '    make clean      cleanup all temporary files'
	@echo

install:
	@test -d "$(VIRTUAL_ENV)" || mkdir -p "$(VIRTUAL_ENV)"
	@test -x "$(VIRTUAL_ENV)/bin/python" || virtualenv --quiet "$(VIRTUAL_ENV)"
	@test -x "$(VIRTUAL_ENV)/bin/pip" || easy_install pip
	@test -x "$(VIRTUAL_ENV)/bin/pip" || pip install --quiet pip
	@pip install --quiet --requirement=requirements.txt
	@pip install --quiet flufl.enum
	@pip uninstall --yes $(PACKAGE_NAME) &>/dev/null || true
	@pip install --quiet --no-deps --ignore-installed .

reset:
	$(MAKE) clean
	rm -Rf "$(VIRTUAL_ENV)"
	$(MAKE) install

check: install
	@scripts/check-code-style.sh

test: install
	@pip install --quiet --requirement=requirements-tests.txt
	@py.test --cov
	@coverage html

tox: install
	@pip install --quiet tox && tox

# The following makefile target isn't documented on purpose, I don't want
# people to execute this without them knowing very well what they're doing.

full-coverage: install
	@pip install --quiet --requirement=requirements-tests.txt
	@scripts/collect-full-coverage.sh
	@coverage html

cog: install
	@echo Installing cog ...
	@pip install --quiet cogapp

releases: cog
	@cog.py -r apt_smart/releases.py

readme: cog
	@cog.py -r README*.rst

docs: releases readme
	@pip install --quiet sphinx
	@cd docs && sphinx-build -nb html -d build/doctrees . build/html

publish: install
	git push origin && git push --tags origin
	$(MAKE) clean
	pip install --quiet twine wheel
	python setup.py sdist bdist_wheel
	twine upload dist/*
	$(MAKE) clean

clean:
	@rm -Rf *.egg .cache .coverage .coverage.* .tox build dist docs/build htmlcov
	@find -depth -type d -name __pycache__ -exec rm -Rf {} \;
	@find -type f -name '*.pyc' -delete

.PHONY: default install reset check test tox readme docs publish clean
