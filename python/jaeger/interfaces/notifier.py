#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-11
# @Filename: notifier.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from typing import Any, Callable, List

from can import Message


__all__ = ["Notifier"]


Listener_co = Callable[..., Coroutine[Message, Any, Any]]


class Notifier:
    def __init__(self):

        self.loop = asyncio.get_running_loop()

        self.listeners: List[Listener_co] = []
        self.buses = []

    def add_listener(self, callback: Listener_co):

        self.listeners.append(callback)

    def add_bus(self, bus):

        self.buses.append(bus)
        asyncio.create_task(self._monitor_bus(bus))

    async def _monitor_bus(self, bus):

        while True:
            msg = await bus.receive()
            await asyncio.gather(*[lstn(msg) for lstn in self.listeners])
