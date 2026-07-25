"""Minimal coroutine runner so pure unit tests need no async test plugin."""
import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        arguments = {name: pyfuncitem.funcargs[name] for name in inspect.signature(pyfuncitem.obj).parameters}
        asyncio.run(pyfuncitem.obj(**arguments))
        return True
    return None
