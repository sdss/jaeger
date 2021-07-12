#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-11
# @Filename: virtual.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING, List


if TYPE_CHECKING:
    from can import Message


queues: List[asyncio.Queue] = []


class VirtualBus:
    def __init__(self, channel: str, /, **kwargs):

        self.channel = channel

        self.queue: asyncio.Queue[Message] = asyncio.Queue()
        queues.append(self.queue)

    def send(self, msg: Message):

        for queue in queues:
            if queue is self.queue:
                continue
            queue.put_nowait(msg)

    async def receive(self):

        msg = await self.queue.get()

        return msg
