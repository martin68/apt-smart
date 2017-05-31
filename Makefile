# Makefile for the 'apt-mirror-updater' package.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: June 29, 2016
# URL: https://apt-mirror-updater.readthedocs.io

WORKON_HOME ?= $(HOME)/.virtualenvs
VIRTUAL_ENV ?= $(WORKON_HOME)/apt-mirror-updater
PATH := $(VIRTUAL_ENV)/bin:$(PATH)
MAKE := $(MAKE) --no-print-directory
SHELL = bash

default:
	@echo 'Makefile for apt-mirror-updater'
	@echo
	@echo 'Usage:'
	@echo
	@echo '    make install   install the package in a virtual environment'
	@echo '    make reset     recreate the virtual environment'
	@echo '    make check     check coding style (PEP-8, PEP-257)'
	@echo '    make readme    update usage in readme'
	@echo '    make docs      update documentation using Sphinx'
	@echo '    make publish   publish changes to GitHub/PyPI'
	@echo '    make clean     cleanup all temporary files'
	@echo

install:
	@test -d "$(VIRTUAL_ENV)" || mkdir -p "$(VIRTUAL_ENV)"
	@test -x "$(VIRTUAL_ENV)/bin/python" || virtualenv --quiet "$(VIRTUAL_ENV)"
	@test -x "$(VIRTUAL_ENV)/bin/pip" || easy_install pip
	@test -x "$(VIRTUAL_ENV)/bin/pip-accel" || (pip install --quiet pip-accel && pip-accel install --quiet 'urllib3[secure]')
	@echo "Installing dependencies .." >&2
	@pip-accel install --quiet --requirement=requirements.txt
	@echo "Updating installation of apt-mirror-updater .." >&2
	@pip uninstall --yes apt-mirror-updater &>/dev/null || true
	@pip install --quiet --no-deps --editable .

reset:
	$(MAKE) clean
	rm -Rf "$(VIRTUAL_ENV)"
	$(MAKE) install

check: install
	@echo "Updating installation of flake8 .." >&2
	@pip-accel install --upgrade --quiet --requirement=requirements-checks.txt
	@flake8

readme: install
	pip-accel install --quiet cogapp
	cog.py -r README.rst

docs: install
	@pip-accel install --quiet sphinx
	@cd docs && sphinx-build -nb html -d build/doctrees . build/html

publish: install
	git push origin && git push --tags origin
	make clean
	pip-accel install --quiet twine wheel
	python setup.py sdist bdist_wheel
	twine upload dist/*
	make clean

clean:
	rm -Rf *.egg .cache .coverage .tox build dist docs/build htmlcov
	find -depth -type d -name __pycache__ -exec rm -Rf {} \;
	find -type f -name '*.pyc' -delete

.PHONY: default install reset check readme docs publish clean
