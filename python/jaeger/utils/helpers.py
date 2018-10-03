#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-14
# @Filename: helpers.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-02 16:17:16

import asyncio


__ALL__ = ['AsyncQueueMixIn', 'StatusMixIn']


class AsyncQueueMixIn(object):
    """Provides a `asyncio.Queue` object with a watcher.

    Parameters
    ----------
    name : str
        The name of the attribute that will contain the queue.
    loop : event loop or `None`
        The current event loop, or `asyncio.get_event_loop`.
    get_callback : callable
        A function to call when a new item is received in the queue. It can
        be a coroutine.

    """

    def __init__(self, name='queue', loop=None, get_callback=None):

        async def process_queue(loop, queue):
            """Waits for the next item and sends it to the cb function."""

            while True:
                item = await queue.get()
                if get_callback:
                    loop.call_soon_threadsafe(get_callback, item)

        setattr(self, name, asyncio.Queue())

        loop = loop or asyncio.get_event_loop()
        queue = getattr(self, name)
        setattr(self, name + '_watcher', loop.create_task(process_queue(loop, queue)))

    def start_timeout_timer(self, loop, watcher, timeout, timeout_callback=None):
        """Starts the timer for timing out the watcher.

        Parameters
        ----------
        loop : event loop or `None`
            The current event loop, or `asyncio.get_event_loop`.
        watcher : `asyncio.Task`
            The task that is running the queue watcher. It will be cancelled
            after the timeout.
        timeout : `int` or `None`
            The time, in seconds, after which the queue will stop being watched
            and ``timeout_callback`` will be called. If `None`, the watcher
            will run indefinitely.
        timeout_callback : callable
            A function to call when the watcher times out. It can be a
            coroutine.

        """

        def cancel_queue_watcher():
            """Cancels the watcher and calls the timout callback."""

            watcher.cancel()

            if timeout_callback:
                loop.call_soon_threadsafe(timeout_callback)

        self.loop.call_later(timeout, cancel_queue_watcher)


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
            func(self)

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
