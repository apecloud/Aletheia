"""Shared hard-timeout wrapper for litellm.completion() calls.

litellm's own ``timeout=`` kwarg doesn't reliably fire on a stuck
proxy/SOCKS CONNECT tunnel -- observed hanging indefinitely (0% CPU, an
ESTABLISHED connection to a local proxy that never responds) during a real
benchmark run. This runs the call in a worker thread and enforces our own
wall-clock timeout via future.result(), with a small grace period added on
top of the caller's timeout.

Extracted from duplicated copies of this exact pattern across
llm/llm_planner.py (7 call sites), agents/node_type_catalog.py,
scripts/hotpotqa_graph_judge.py, and scripts/passage_relation_extraction.py.
"""

from __future__ import annotations

from concurrent.futures import Executor, TimeoutError as FutureTimeoutError
from typing import Any, Callable


def call_with_hard_timeout(executor: Executor, fn: Callable, *, timeout: float, grace: float = 15, **kwargs: Any) -> Any:
    """Submit ``fn(**kwargs)`` to ``executor`` and wait at most ``timeout + grace``
    seconds.

    Raises ``TimeoutError`` if the wait itself elapses with no response. If
    ``fn`` raises its own error (including its own timeout) before our wait
    elapses, that original exception propagates as-is instead of being
    masked -- ``future.done()`` is what tells the two cases apart: True
    means ``fn`` already ran and raised on its own (Python 3.11+ makes
    ``concurrent.futures.TimeoutError`` an alias of the builtin
    ``TimeoutError``, so a timeout raised BY ``fn`` itself lands here too);
    False means our wait genuinely elapsed with no response.
    """
    future = executor.submit(fn, **kwargs)
    try:
        return future.result(timeout=timeout + grace)
    except FutureTimeoutError:
        if future.done():
            raise
        future.cancel()
        raise TimeoutError(f"hard timeout: no response after {timeout + grace:.0f}s (proxy/connect hang)")
