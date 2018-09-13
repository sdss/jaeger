#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-04
# @Filename: bus.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-12 20:44:07


import asyncio
import os
import pty
import re

import serial

import can
from can.bus import BusABC


class BusTester(BusABC):

    def __init__(self, fps=None):

        self.fps = fps
        self._reply_queue = asyncio.Queue()

        super().__init__('')

    def _recv_internal(self, timeout=None):
        """Retrieves and returns a message from the internal reply queue."""

        async def coro(timeout):
            try:
                reply = await asyncio.wait_for(self._reply_queue.get(), timeout, loop=self.loop)
                assert isinstance(reply, can.Message)
                return reply, False
            except asyncio.TimeoutError:
                return None, False

        return self.loop.run_until_complete(coro(timeout))

    def send(self, message):
        """Receives and processes a message."""

        pass


class SlcanBusTester(BusTester):

    def __init__(self, *args, **kwargs):

        self._master, self._slave = pty.openpty()
        s_name = os.ttyname(self._slave)

        self.serialPortOrig = serial.Serial(s_name)

        super().__init__()

    def write(self, string):

        if not string.endswith('\r'):
            string += '\r'

        msg = string.encode()

        if msg in [b'O\r', b'C\r']:
            os.write(self._master, b'\r')
        elif re.match(b'S[0-9]+', msg):
            os.write(self._master, b'\r')
        else:
            os.write(self._master, b'\x07\r')
