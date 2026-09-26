"""Run expensive jobs one at a time on a small single-process server (4 GB VPS)."""

import os
import threading
from functools import wraps

_CPU_WORK = threading.RLock()


def serialized_on_cpu(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if os.getenv("BRANDDECK_CPU_MODE", "0") == "1":
            with _CPU_WORK:
                return function(*args, **kwargs)
        return function(*args, **kwargs)

    return wrapped
