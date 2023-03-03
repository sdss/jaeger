#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-11
# @Filename: virtual.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import Dict, List

from jaeger.interfaces.bus import BusABC

from .message import Message


queues: Dict[str, List[asyncio.Queue]] = {}


class VirtualBus(BusABC):
    """A class implementing a virtual CAN bus that listens to messages on a channel."""

    def __init__(self, channel: str):
        self.channel = channel

        self.queue: asyncio.Queue[Message] = asyncio.Queue()

        if self.channel not in queues:
            queues[self.channel] = [self.queue]
        else:
            queues[self.channel].append(self.queue)

    def send(self, msg: Message):
        """Send message to the virtual bus (self does not receive a copy)."""

        for queue in queues[self.channel]:
            if queue is self.queue:
                continue
            queue.put_nowait(msg)

    async def get(self):
        """Get messages from the bus."""

        msg = await self.queue.get()

        return msg
