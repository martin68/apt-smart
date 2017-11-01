# Automated, robust apt-get mirror selection for Debian and Ubuntu.
#
# Author: Peter Odding <peter@peterodding.com>
# Last Change: October 31, 2017
# URL: https://apt-mirror-updater.readthedocs.io

"""Simple, robust and concurrent HTTP requests (designed for one very narrow use case)."""

# Standard library modules.
import logging
import multiprocessing
import signal

# External dependencies.
from humanfriendly import Timer, format_size
from six.moves.urllib.request import urlopen
from stopit import SignalTimeout, TimeoutException

# Initialize a logger for this module.
logger = logging.getLogger(__name__)

# Stop the `stopit' logger from logging tracebacks.
logging.getLogger('stopit').setLevel(logging.ERROR)


def fetch_url(url, timeout=10, retry=False, max_attempts=3):
    """
    Fetch a URL, optionally retrying on failure.

    :param url: The URL to fetch (a string).
    :param timeout: The maximum time in seconds that's allowed to pass before
                    the request is aborted (a number, defaults to 10 seconds).
    :param retry: Whether to retry on failure (defaults to :data:`False`).
    :param max_attempts: The maximum number of attempts when retrying is
                         enabled (an integer, defaults to three).
    :returns: The response body (a byte string).
    :raises: Any of the following exceptions can be raised:

             - :exc:`NotFoundError` when the URL returns a 404 status code.
             - :exc:`InvalidResponseError` when the URL returns a status code
               that isn't 200.
             - :exc:`stopit.TimeoutException` when the request takes longer
               than `timeout` seconds (refer to the `stopit documentation
               <https://pypi.python.org/pypi/stopit>`_ for details).
             - Any exception raised by Python's standard library in the last
               attempt (assuming all attempts raise an exception).
    """
    timer = Timer()
    logger.debug("Fetching %s ..", url)
    for i in range(1, max_attempts + 1):
        try:
            with SignalTimeout(timeout, swallow_exc=False):
                response = urlopen(url)
                status_code = response.getcode()
                if status_code != 200:
                    exc_type = (NotFoundError if status_code == 404 else InvalidResponseError)
                    raise exc_type("URL returned unexpected status code %s! (%s)" % (status_code, url))
                response_body = response.read()
                logger.debug("Took %s to fetch %s.", timer, url)
                return response_body
        except (NotFoundError, TimeoutException):
            # We never retry 404 responses and timeouts.
            raise
        except Exception as e:
            if retry and i < max_attempts:
                logger.warning("Failed to fetch %s, retrying (%i/%i, error was: %s)", url, i, max_attempts, e)
            else:
                raise


def fetch_concurrent(urls, concurrency=None):
    """
    Fetch the given URLs concurrently using :mod:`multiprocessing`.

    :param urls: An iterable of URLs (strings).
    :param concurrency: Override the concurrency (an integer, defaults to the
                        value computed by :func:`get_default_concurrency()`).
    :returns: A list of tuples like those returned by :func:`fetch_worker()`.
    """
    if concurrency is None:
        concurrency = get_default_concurrency()
    pool = multiprocessing.Pool(concurrency)
    try:
        return pool.map(fetch_worker, urls, chunksize=1)
    finally:
        pool.terminate()


def get_default_concurrency():
    """
    Get the default concurrency for :func:`fetch_concurrent()`.

    :returns: A positive integer number.
    """
    return max(4, multiprocessing.cpu_count() * 2)


def fetch_worker(url):
    """
    Fetch the given URL for :func:`fetch_concurrent()`.

    :param url: The URL to fetch (a string).
    :returns: A tuple of three values:

              1. The URL that was fetched (a string).
              2. The data that was fetched (a string or :data:`None`).
              3. The number of seconds it took to fetch the URL (a number).
    """
    # Ignore Control-C instead of raising KeyboardInterrupt because (due to a
    # quirk in multiprocessing) this can cause the parent and child processes
    # to get into a deadlock kind of state where only Control-Z will get you
    # your precious terminal back; super annoying IMHO.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    timer = Timer()
    try:
        data = fetch_url(url, retry=False)
    except Exception as e:
        logger.debug("Failed to fetch %s! (%s)", url, e)
        data = None
    else:
        kbps = format_size(round(len(data) / timer.elapsed_time, 2))
        logger.debug("Downloaded %s at %s per second.", url, kbps)
    return url, data, timer.elapsed_time


class InvalidResponseError(Exception):

    """Raised by :func:`fetch_url()` when a URL returns a status code that isn't 200."""


class NotFoundError(InvalidResponseError):

    """Raised by :func:`fetch_url()` when a URL returns a 404 status code."""
