"""Shared Prometheus metrics and helpers for tracking Huey queues."""

from __future__ import annotations

import inspect
import threading
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, Dict, TypeVar, cast

import prometheus_client

try:  # Pydantic may be absent in some environments.
    from pydantic import ValidationError
except Exception:  # pragma: no cover - fallback when pydantic unavailable

    class ValidationError(Exception):  # type: ignore[misc]
        """Fallback ValidationError so type checks continue to work."""


try:  # Huey may be absent when metrics are imported in isolation.
    from huey.exceptions import HueyException
except Exception:  # pragma: no cover - fallback when huey unavailable

    class HueyException(Exception):  # type: ignore[misc]
        """Fallback HueyException for environments without huey."""


REGISTRY = prometheus_client.CollectorRegistry()

HUEY_QUEUE_GAUGE = prometheus_client.Gauge(
    "huey_queue",
    "Number of tasks in Huey queues grouped by state",
    labelnames=["queue", "state"],
    registry=REGISTRY,
)

BOT_REQUESTS = prometheus_client.Counter(
    "bot_requests_total",
    "Value: bot_requests_total",
    labelnames=["type", "chat_type", "mode"],
    registry=REGISTRY,
)


STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_RESULTS = "results"
STATE_FAILED = "failed"


_GaugeState = Dict[str, float]
_GaugeRegistry = Dict[str, _GaugeState]
_state_counts: _GaugeRegistry = defaultdict(lambda: defaultdict(float))
_state_lock = threading.RLock()

F = TypeVar("F", bound=Callable[..., Any])


def _set_queue_state(queue: str, state: str, value: float) -> float:
    sanitized = value if value > 0 else 0.0
    HUEY_QUEUE_GAUGE.labels(queue=queue, state=state).set(sanitized)
    return sanitized


def adjust_queue_state(queue: str, state: str, delta: float) -> float:
    """Adjust *state* for *queue* by *delta* and return the new value."""

    with _state_lock:
        states = _state_counts[queue]
        new_value = states.get(state, 0.0) + delta
        if new_value < 0:
            new_value = 0.0
        states[state] = new_value
        return _set_queue_state(queue, state, new_value)


def set_queue_state(queue: str, state: str, value: float) -> float:
    """Force *state* for *queue* to *value* (clamped at zero)."""

    with _state_lock:
        states = _state_counts[queue]
        states[state] = value if value > 0 else 0.0
        return _set_queue_state(queue, state, states[state])


def get_queue_state(queue: str, state: str) -> float:
    """Return the cached value for *state* in *queue* (defaults to 0)."""

    with _state_lock:
        return _state_counts[queue].get(state, 0.0)


def track_huey_enqueue(queue: str) -> Callable[[F], F]:
    """Decorator to mark tasks as pending when enqueued via Huey wrappers."""

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                adjust_queue_state(queue, STATE_PENDING, 1.0)
                try:
                    return await func(*args, **kwargs)
                except (ValueError, ValidationError, FileNotFoundError, HueyException):
                    adjust_queue_state(queue, STATE_PENDING, -1.0)
                    raise

            return cast(F, async_wrapper)

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            adjust_queue_state(queue, STATE_PENDING, 1.0)
            try:
                return func(*args, **kwargs)
            except (ValueError, ValidationError, FileNotFoundError, HueyException):
                adjust_queue_state(queue, STATE_PENDING, -1.0)
                raise

        return cast(F, wrapper)

    return decorator


def consume_huey_result(queue: str, amount: float = 1.0) -> float:
    """Reduce the outstanding result count for *queue* by *amount*."""

    return adjust_queue_state(queue, STATE_RESULTS, -abs(amount))


__all__ = [
    "REGISTRY",
    "BOT_REQUESTS",
    "HUEY_QUEUE_GAUGE",
    "STATE_PENDING",
    "STATE_RUNNING",
    "STATE_RESULTS",
    "STATE_FAILED",
    "adjust_queue_state",
    "set_queue_state",
    "get_queue_state",
    "track_huey_enqueue",
    "consume_huey_result",
]
