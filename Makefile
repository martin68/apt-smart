# Makefile for the 'apt-mirror-updater' package.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: March 10, 2016
# URL: https://apt-mirror-updater.readthedocs.org

WORKON_HOME ?= $(HOME)/.virtualenvs
VIRTUAL_ENV ?= $(WORKON_HOME)/apt-mirror-updater
PATH := $(VIRTUAL_ENV)/bin:$(PATH)
MAKE := $(MAKE) --no-print-directory

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
	@test -x "$(VIRTUAL_ENV)/bin/pip-accel" || pip install --quiet pip-accel
	@pip uninstall -y apt-mirror-updater 1>/dev/null 2>&1 || true
	@pip-accel install --quiet --editable .

reset:
	$(MAKE) clean
	rm -Rf "$(VIRTUAL_ENV)"
	$(MAKE) install

check: install
	test -x "$(VIRTUAL_ENV)/bin/flake8" || pip-accel install --quiet flake8-pep257
	flake8

readme:
	test -x "$(VIRTUAL_ENV)/bin/cog.py" || pip-accel install --quiet cogapp
	cog.py -r README.rst

docs: install
	test -x "$(VIRTUAL_ENV)/bin/sphinx-build" || pip-accel install --quiet sphinx
	cd docs && sphinx-build -b html -d build/doctrees . build/html

publish:
	git push origin && git push --tags origin
	make clean && python setup.py sdist upload

clean:
	rm -Rf *.egg *.egg-info .coverage .tox build dist docs/build htmlcov
	find -depth -type d -name __pycache__ -exec rm -Rf {} \;
	find -type f -name '*.pyc' -delete

.PHONY: default install reset check readme docs publish clean
