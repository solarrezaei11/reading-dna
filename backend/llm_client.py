"""Shared LLM call-concurrency and timeout enforcement.

Every outbound LLM request in the backend — Reading DNA generation, battle
recommendations (both models), both judge calls (Cerebras + Ollama),
prediction calls, and map/embeddings cluster naming — must go through
`call_with_limit` so a single process-wide semaphore actually bounds paid
LLM concurrency (MAX_LLM_CONCURRENCY), instead of each module maintaining
its own private semaphore that only bounds its own call sites.

Cancellation safety: `call_with_limit` never converts asyncio.CancelledError
into a TimeoutError or any other exception — cancellation always propagates
as CancelledError so callers (and asyncio itself) can tell "the caller gave
up on us" apart from "the model call genuinely timed out".
"""
import asyncio
import logging
from typing import Awaitable, Optional, TypeVar

from config import LLM_ATTEMPT_TIMEOUT_SECONDS, MAX_LLM_CONCURRENCY

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Single process-wide semaphore bounding every outbound LLM call.
LLM_SEMAPHORE = asyncio.Semaphore(MAX_LLM_CONCURRENCY)
_tracked_semaphore = LLM_SEMAPHORE
_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _semaphore_for_current_loop() -> asyncio.Semaphore:
    """Reuse the process semaphore, rebuilding it only after its old event
    loop has closed (common in isolated async tests and safe because that
    loop can no longer have live work). Production normally has one loop."""
    global LLM_SEMAPHORE, _tracked_semaphore, _semaphore_loop
    current_loop = asyncio.get_running_loop()

    # Tests may replace the public semaphore with a local instance. Track
    # that identity change without reaching into asyncio's private `_loop`.
    if LLM_SEMAPHORE is not _tracked_semaphore:
        _tracked_semaphore = LLM_SEMAPHORE
        _semaphore_loop = current_loop
        return LLM_SEMAPHORE

    if _semaphore_loop is None:
        _semaphore_loop = current_loop
    elif _semaphore_loop is not current_loop:
        if not _semaphore_loop.is_closed():
            raise RuntimeError("The shared LLM semaphore is active on another event loop.")
        LLM_SEMAPHORE = asyncio.Semaphore(MAX_LLM_CONCURRENCY)
        _tracked_semaphore = LLM_SEMAPHORE
        _semaphore_loop = current_loop
    return LLM_SEMAPHORE


async def call_with_limit(
    coro: Awaitable[T],
    timeout: Optional[float] = None,
    queue_timeout: Optional[float] = None,
) -> T:
    """Run `coro` under the shared LLM semaphore with a finite per-attempt
    timeout. Raises `asyncio.TimeoutError` if the call doesn't complete in
    time, or re-raises whatever `coro` raised otherwise. `asyncio.CancelledError`
    is never suppressed or translated — it always propagates so outer
    cancellation (e.g. a client disconnect) is never mistaken for a normal
    model error.

    The wait to ACQUIRE the semaphore itself is also bounded (`queue_timeout`,
    defaulting to the same value as `timeout`) — otherwise a request could
    queue behind a burst of concurrent calls indefinitely before its actual
    per-attempt timeout even starts counting. If cancelled or timed out while
    still waiting for the semaphore, `coro` is closed (when it's a plain
    coroutine object) before the exception propagates, so an unstarted
    coroutine never lingers as a "coroutine was never awaited" warning and
    never has a chance to open any resources it would have.
    """
    effective_timeout = LLM_ATTEMPT_TIMEOUT_SECONDS if timeout is None else timeout
    effective_queue_timeout = effective_timeout if queue_timeout is None else queue_timeout
    semaphore = _semaphore_for_current_loop()

    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=effective_queue_timeout)
    except (asyncio.CancelledError, asyncio.TimeoutError, RuntimeError):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise

    try:
        return await asyncio.wait_for(coro, timeout=effective_timeout)
    finally:
        semaphore.release()
