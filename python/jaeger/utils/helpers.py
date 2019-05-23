#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-14
# @Filename: helpers.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-05-22 19:06:59

import asyncio
from concurrent.futures import Executor
from contextlib import suppress
from threading import Thread


__ALL__ = ['AsyncQueue', 'StatusMixIn', 'Poller', 'AsyncioExecutor', 'as_complete_failer']


class AsyncQueue(asyncio.Queue):
    """Provides an `asyncio.Queue` object with a watcher.

    Parameters
    ----------
    loop : event loop or `None`
        The current event loop, or `asyncio.get_event_loop`.
    callback : callable
        A function to call when a new item is received from the queue. It can
        be a coroutine.

    """

    def __init__(self, loop=None, callback=None):

        async def process_queue(loop):
            """Waits for the next item and sends it to the cb function."""

            while True:
                item = await self.get()
                if callback:
                    loop.call_soon_threadsafe(callback, item)

        super().__init__()

        loop = loop or asyncio.get_event_loop()

        self.watcher = loop.create_task(process_queue(loop))


class StatusMixIn(object):
    """A mixin that provides status tracking with callbacks.

    Provides a status property that executes a list of callbacks when
    the status changes.

    Parameters
    ----------
    maskbit_flags : class
        A class containing the available statuses as a series of maskbit
        flags. Usually as subclass of `enum.Flag`.
    initial_status : str
        The initial status.
    callback_func : function
        The function to call if the status changes.
    call_now : bool
        Whether the callback function should be called when initialising.

    Attributes
    ----------
    callbacks : list
        A list of the callback functions to call.

    """

    def __init__(self, maskbit_flags, initial_status=None,
                 callback_func=None, call_now=False):

        self.flags = maskbit_flags
        self.callbacks = []
        self._status = initial_status
        self.watcher = None

        if callback_func is not None:
            self.callbacks.append(callback_func)

        if call_now is True:
            self.do_callbacks()

    def add_callback(self, cb):
        """Adds a callback."""

        self.callbacks.append(cb)

    def remove_callback(self, cb):
        """Removes a callback."""

        self.callbacks.remove(cb)

    def do_callbacks(self):
        """Calls functions in ``callbacks``."""

        assert hasattr(self, 'callbacks'), \
            'missing callbacks attribute. Did you call __init__()?'

        for func in self.callbacks:
            func()

    @property
    def status(self):
        """Returns the status."""

        return self._status

    @status.setter
    def status(self, value):
        """Sets the status."""

        if value != self._status:
            self._status = self.flags(value)
            self.do_callbacks()
            if self.watcher is not None:
                self.watcher.set()

    async def wait_for_status(self, value, loop=None):
        """Awaits until the status matches ``value``."""

        if self.status == value:
            return

        if loop is None:
            if hasattr(self, 'loop') and self.loop is not None:
                loop = self.loop
            else:
                loop = asyncio.get_event_loop()

        self.watcher = asyncio.Event(loop=loop)

        while self.status != value:
            await self.watcher.wait()
            if self.watcher is not None:
                self.watcher.clear()

        self.watcher = None


class Poller(object):
    """A task that runs a callback periodically.

    Parameters
    ----------
    callback : function or coroutine
        A function or coroutine to call periodically.
    delay : float
        Initial delay between calls to the callback.
    loop : event loop
        The event loop to which to attach the task.

    """

    def __init__(self, callback, delay=1, loop=None):

        self.callback = callback

        self._orig_delay = delay
        self.delay = delay

        self.loop = loop or asyncio.get_event_loop()

        # Create two tasks, one for the sleep timer and another for the poller
        # itself. We do this because we want to be able to cancell the sleep
        # coroutine if we are going to change the delay.
        self._sleep_task = None
        self._task = None

    async def poller(self):
        """The polling loop."""

        while True:

            if asyncio.iscoroutinefunction(self.callback):
                await asyncio.create_task(self.callback())
            else:
                self.callback()

            self._sleep_task = asyncio.create_task(asyncio.sleep(self.delay))

            await self._sleep_task

    async def set_delay(self, delay=None):
        """Sets the delay for polling.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If `None`, restores the
            original delay."""

        await self.stop()
        await self.start(delay)

    async def start(self, delay=None):
        """Starts the poller.

        Parameters
        ----------
        delay : float
            The delay between calls to the callback. If not specified,
            restores the original delay used when the class was instantiated.

        """

        if self.running:
            raise RuntimeError('poller is already running.')

        self.delay = delay or self._orig_delay
        self._task = asyncio.create_task(self.poller())

    async def stop(self):
        """Cancel the poller."""

        if not self.running:
            return

        self._task.cancel()

        with suppress(asyncio.CancelledError):
            await self._task

    @property
    def running(self):
        """Returns `True` if the poller is running."""

        if self._task and not self._task.cancelled():
            return True

        return False


class AsyncioExecutor(Executor):
    """An executor to run coroutines from a normal function.

    Copied from `http://bit.ly/2IYmqzN`__.

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


async def as_complete_failer(aws, on_fail_callback=None, **kwargs):
    """Similar to `~asyncio.as_complete` but cancels all the tasks
    if any of them returns `False`.

    Parameters
    ----------
    aws : list
        A list of awaitable objects.
    on_fail_callback
        A function or coroutine to call if any of the tasks failed.
    kwargs : dict
        A dictionary of keywords to be passed to `~asyncio.as_complete`.

    Returns
    -------
    result : bool
        `True` if all the tasks complete, `False` if any of them failed and
        the rest were cancelled.

    """

    loop = kwargs.get('loop', asyncio.get_event_loop())

    tasks = [loop.create_task(aw) for aw in aws]

    failed = False
    for next_completed in asyncio.as_completed(tasks, **kwargs):
        if not await next_completed:
            failed = True
            break

    if failed:

        # Cancel tasks
        [task.cancel() for task in tasks]

        with suppress(asyncio.CancelledError):
            await asyncio.gather(*[task for task in tasks])

        if on_fail_callback:
            if asyncio.iscoroutinefunction(on_fail_callback):
                await on_fail_callback()
            else:
                on_fail_callback()

        return False

    return True
