import time
from collections import defaultdict
from typing import Tuple

_request_counts = defaultdict(list)
_fail_counts = defaultdict(int)

MAX_REQUESTS_PER_MINUTE = 30
MAX_FAILS_BEFORE_BAN = 5

def check_rate_limit(key: str) -> Tuple[bool, int]:
    now = time.time()
    _request_counts[key] = [t for t in _request_counts[key] if now - t < 60]

    if len(_request_counts[key]) >= MAX_REQUESTS_PER_MINUTE:
        return False, 0

    _request_counts[key].append(now)
    return True, MAX_REQUESTS_PER_MINUTE - len(_request_counts[key])

def record_failed_attempt(key: str) -> int:
    _fail_counts[key] += 1
    return _fail_counts[key]

def reset_failed_attempts(key: str):
    _fail_counts.pop(key, None)
