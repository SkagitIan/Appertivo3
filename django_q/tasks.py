from __future__ import annotations

from importlib import import_module
from typing import Any


def async_task(func_path: str, *args: Any, **kwargs: Any) -> Any:
    """A lightweight stand-in for Django-Q's async_task.

    The stub executes tasks immediately, which keeps tests deterministic while
    satisfying the dependency on the Django-Q2 interface.
    """

    module_path, func_name = func_path.rsplit(".", 1)
    module = import_module(module_path)
    func = getattr(module, func_name)
    return func(*args, **kwargs)
