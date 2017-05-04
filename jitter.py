from functools import wraps
from time import sleep
from random import random
import logging

log = logging.getLogger(__name__)

def delay(n=50, base=0.05, cap=300):
    """Provide jittery exponential backoff

    The delay between iterations on exceptions is calculated using the
    "Equal Jitter" algorithm from
    https://www.awsarchitectureblog.com/2015/03/backoff.html.

    """

    def g(f):
        @wraps(f)
        def d(*args, **kwargs):
            for i in range(1, n + 1):
                try:
                    return f(*args, **kwargs)
                except Exception as e:
                    z = min(base * 2 ** i, cap) / 2
                    d = z + random() * z
                    log.warn(
                        "Try %d: Caught an exception (%s) during processing. Backing off for %7.3fs",
                        i, e, d)
                    sleep(d)
            log.error("Failed after %d attempts.", n)
        return d
    return g

@delay()
def test():
    """Demonstrates using the delay decorator."""
    print("Hello")
    raise Exception("Raising")
