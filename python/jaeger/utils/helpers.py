#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-14
# @Filename: helpers.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import concurrent.futures
import enum
import warnings
from concurrent.futures import Executor
from contextlib import suppress
from functools import partial
from threading import Thread

from typing import Callable, Generic, Optional, Type, TypeVar


__all__ = [
    "AsyncQueue",
    "StatusMixIn",
    "PollerList",
    "Poller",
    "AsyncioExecutor",
    "run_in_executor",
]


class AsyncQueue(asyncio.Queue):
    """Provides an `asyncio.Queue` object with a watcher.

    Parameters
    ----------
    callback
        A function to call when a new item is received from the queue. It can
        be a coroutine.

    """

    def __init__(self, callback: Optional[Callable] = None):
        async def process_queue():
            """Waits for the next item and sends it to the cb function."""

            loop = asyncio.get_running_loop()
            while True:
                item = await self.get()
                if callback:
                    loop.call_soon_threadsafe(callback, item)

        super().__init__()

        self.watcher = asyncio.create_task(process_queue())


Status_co = TypeVar("Status_co", bound=enum.Enum)


class StatusMixIn(Generic[Status_co]):
    """A mixin that provides status tracking with callbacks.

    Provides a status property that executes a list of callbacks when
    the status changes.

    Parameters
    ----------
    maskbit_flags
        A class containing the available statuses as a series of maskbit
        flags. Usually as subclass of `enum.Flag`.
    initial_status
        The initial status.
    callback_func
        The function to call if the status changes.
    call_now
        Whether the callback function should be called when initialising.

    Attributes
    ----------
    callbacks
        A list of the callback functions to call.

    """

    def __init__(
        self,
        maskbit_flags: Type[Status_co],
        initial_status: Optional[Status_co] = None,
        callback_func: Optional[Callable] = None,
        call_now: bool = False,
    ):

        self._flags = maskbit_flags
        self.callbacks = []
        self._status: Optional[Status_co] = initial_status
        self.watcher = None

        if callback_func is not None:
            self.callbacks.append(callback_func)

        if call_now is True:
            self.do_callbacks()

    def add_callback(self, cb: Callable):
        """Adds a callback."""

        self.callbacks.append(cb)

    def remove_callback(self, cb: Callable):
        """Removes a callback."""

        self.callbacks.remove(cb)

    def do_callbacks(self):
        """Calls functions in ``callbacks``."""

        assert hasattr(
            self, "callbacks"
        ), "missing callbacks attribute. Did you call __init__()?"

        for func in self.callbacks:
            func()

    @property
    def status(self) -> Status_co:
        """Returns the status."""

        if self._status is None:
            raise ValueError("status not set.")

        return self._status

    @status.setter
    def status(self, value: Status_co):
        """Sets the status."""

        if value != self._status:
            self._status = self.flags(value)
            self.do_callbacks()
            if self.watcher is not None:
                self.watcher.set()

    @property
    def flags(self):
        """Gets the flags associated to this status."""

        return self._flags

    @flags.setter
    def flags(self, value: Type[Status_co]):
        """Sets the flags associated to this status."""

        self._flags = value
        self._status = None

    async def wait_for_status(
        self,
        value,
    ):
        """Awaits until the status matches ``value``."""

        if self.status == value:
            return

        self.watcher = asyncio.Event()

        while self.status != value:
            await self.watcher.wait()
            if self.watcher is not None:
                self.watcher.clear()

        self.watcher = None


class PollerList(list):
    """A list of `.Poller` to be managed jointly."""

    def __init__(self, pollers=[]):

        names = [poller.name for poller in pollers]
        assert len(names) == len(set(names)), "repeated names in poller list."

        list.__init__(self, pollers)

    @property
    def names(self):
        """List the poller names."""

        return [poller.name for poller in self]

    def append(self, poller):
        """Adds a poller."""

        assert isinstance(poller, Poller), "not a poller."

        names = [pp.name for pp in self]
        if poller.name in names:
            raise ValueError(
                f"a poller with name {poller.name} is already in the list."
            )

        list.append(self, poller)

    def __getattr__(self, name):
        """Gets a poller by its name."""

        for poller in self:
            if name == poller.name:
                return poller

        return list.__getitem__(self, name)

    def __getitem__(self, item):
        """Gets the poller by name."""

        if isinstance(item, str):
            return self.__getattr__(item)

        return list.__getitem__(self, item)

    async def set_delay(self, delay=None, immediate=False):
        """Sets the delay for all the pollers.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If `None`, restores the
            original delay.
        immediate : bool
            If `True`, stops the currently running tasks and sets the
            new delay. Otherwise waits for the current tasks to complete.

        """

        delay_coros = [
            poller.set_delay(delay=delay, immediate=immediate) for poller in self
        ]
        await asyncio.gather(*delay_coros)

    def start(self, delay=None):
        """Starts all the pollers.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If not specified,
            uses the default delays for each poller.

        """

        for poller in self:
            poller.start(delay=delay)

    async def stop(self):
        """Cancels all the poller."""

        stop_coros = [poller.stop() for poller in self]
        await asyncio.gather(*stop_coros)

    @property
    def running(self):
        """Returns `True` if at least one poller is running."""

        return any([poller.running for poller in self])


class Poller(object):
    """A task that runs a callback periodically.

    Parameters
    ----------
    name : str
        The name of the poller.
    callback : function or coroutine
        A function or coroutine to call periodically.
    delay : float
        Initial delay between calls to the callback.
    loop : event loop
        The event loop to which to attach the task.

    """

    def __init__(self, name, callback, delay=1, loop=None):

        self.name = name
        self.callback = callback

        self._orig_delay = delay
        self.delay = delay

        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()

        # Create two tasks, one for the sleep timer and another for the poller
        # itself. We do this because we want to be able to cancell the sleep
        # coroutine if we are going to change the delay.
        self._sleep_task = None
        self._task = None

    async def poller(self):
        """The polling loop."""

        if self._task is None:
            raise RuntimeError("Task is not running.")

        while True:

            try:
                if asyncio.iscoroutinefunction(self.callback):
                    await self.callback()
                else:
                    self.callback()
            except Exception as ee:
                if ee.__class__ == asyncio.CancelledError:
                    raise
                if not self._task.cancelled:
                    self.loop.call_exception_handler(
                        {"message": "failed running callback", "exception": ee}
                    )

            self._sleep_task = self.loop.create_task(asyncio.sleep(self.delay))

            await self._sleep_task

    async def set_delay(self, delay=None, immediate=False):
        """Sets the delay for polling.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If `None`, restores the
            original delay.
        immediate : bool
            If `True`, stops the currently running task and sets the
            new delay. Otherwise waits for the current task to complete.

        """

        # Only change delay if the difference is significant.
        if delay and abs(self.delay - delay) < 1e-6:
            return

        if not self.running:
            return

        if immediate:
            await self.stop()
            self.start(delay)
        else:
            self.delay = delay or self._orig_delay

    def start(self, delay=None):
        """Starts the poller.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If not specified,
            restores the original delay used when the class was instantiated.

        """

        self.delay = delay or self._orig_delay

        if self.running:
            return

        self._task = self.loop.create_task(self.poller())

        return self

    async def stop(self):
        """Cancel the poller."""

        if self._task is None or not self.running:
            return

        self._task.cancel()

        with suppress(asyncio.CancelledError):
            if self._task is not None:
                await self._task

    async def call_now(self):
        """Calls the callback immediately."""

        restart = False
        delay = self.delay
        if self.running:
            await self.stop()
            restart = True

        if asyncio.iscoroutinefunction(self.callback):
            await self.loop.create_task(self.callback())
        else:
            self.callback()

        if restart:
            self.start(delay=delay)

    @property
    def running(self):
        """Returns `True` if the poller is running."""

        if self._task and not self._task.cancelled():
            return True

        return False


class AsyncioExecutor(Executor):
    """An executor to run coroutines from a normal function.

    Copied from http://bit.ly/2IYmqzN.

    To use, do ::

        with AsyncioExecutor() as executor:
            future = executor.submit(asyncio.sleep, 1)

    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = Thread(target=self._target)
        self._thread.start()

    def _target(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, fn, *args, **kwargs):
        """Submit a coroutine to the executor."""

        coro = fn(*args, **kwargs)
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self, wait=True):
        self._loop.call_soon_threadsafe(self._loop.stop)
        if wait:
            self._thread.join()


async def run_in_executor(fn, *args, catch_warnings=False, executor="thread", **kwargs):
    """Runs a function in an executor.

    In addition to streamlining the use of the executor, this function
    catches any warning issued during the execution and reissues them
    after the executor is done. This is important when using the
    actor log handler since inside the executor there is no loop that
    CLU can use to output the warnings.

    In general, note that the function must not try to do anything with
    the actor since they run on different loops.

    """

    fn = partial(fn, *args, **kwargs)

    if executor == "thread":
        executor = concurrent.futures.ThreadPoolExecutor
    elif executor == "process":
        executor = concurrent.futures.ProcessPoolExecutor
    else:
        raise ValueError("Invalid executor name.")

    if catch_warnings:
        with warnings.catch_warnings(record=True) as records:
            with executor() as pool:
                result = await asyncio.get_event_loop().run_in_executor(pool, fn)

        for ww in records:
            warnings.warn(ww.message, ww.category)

    else:
        with executor() as pool:
            result = await asyncio.get_running_loop().run_in_executor(pool, fn)

    return result
