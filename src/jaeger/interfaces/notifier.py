#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-11
# @Filename: notifier.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, Any, Callable, Coroutine, List, TypeVar

from .message import Message


if TYPE_CHECKING:
    from .bus import BusABC

__all__ = ["Notifier"]


Listener_co = Callable[..., Coroutine[Message, Any, Any]]
Bus_co = TypeVar("Bus_co", bound="BusABC")


class Notifier:
    """Notifier class to report bus messages to multiple listeners."""

    def __init__(self, listeners: List[Listener_co] = [], buses: List[Bus_co] = []):
        self.loop = asyncio.get_running_loop()

        self.listeners = listeners

        self.tasks: list[asyncio.Task] = []

        self.buses: List[BusABC] = []
        for bus in buses:
            self.add_bus(bus)

    def stop(self):
        """Stops the notifier."""

        for task in self.tasks:
            task.cancel()

        self.buses = []

    def add_listener(self, callback: Listener_co):
        """Adds a listener."""

        self.listeners.append(callback)

    def add_bus(self, bus):
        """Adds a bus to monitor."""

        self.buses.append(bus)
        self.tasks.append(asyncio.create_task(self._monitor_bus(bus)))

    def remove_notifier(self, callback: Listener_co):
        """Removes a listener."""

        if callback in self.listeners:
            self.listeners.remove(callback)

    async def _monitor_bus(self, bus: BusABC):
        """Monitors buses and calls the listeners when a message is received."""

        while True:
            msg = await bus.get()
            if msg is not None:
                for listener in self.listeners:
                    asyncio.create_task(listener(msg))
