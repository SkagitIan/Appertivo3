from __future__ import annotations

from importlib import import_module
from typing import Any, Dict


# Queue-specific kwargs supported by Django-Q that should not reach the task.
QUEUE_OPTION_KEYS = {
    "ack_failure",
    "ack_late",
    "broker",
    "bulk",
    "cached",
    "channel",
    "count",
    "delay",
    "group",
    "group_meta",
    "hook",
    "iter_then",
    "iterable",
    "meta",
    "priority",
    "queue",
    "result",
    "retries",
    "retry",
    "save",
    "status",
    "sync",
    "timeout",
    "user",
}


def async_task(func_path: str, *args: Any, **kwargs: Any) -> Any:
    """A lightweight stand-in for Django-Q's async_task.

    The stub executes tasks immediately, which keeps tests deterministic while
    satisfying the dependency on the Django-Q2 interface.
    """

    call_kwargs: Dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in QUEUE_OPTION_KEYS or key == "q_options":
            continue
        call_kwargs[key] = value

    module_path, func_name = func_path.rsplit(".", 1)
    module = import_module(module_path)
    func = getattr(module, func_name)
    return func(*args, **call_kwargs)
