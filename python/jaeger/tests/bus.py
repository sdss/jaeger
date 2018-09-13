#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-04
# @Filename: bus.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-06 17:36:33


import os
import pty
import re

import serial
from can.bus import BusABC


class BusTester(BusABC):

    def __init__(self):

        self._reply_queue = []

        super().__init__('')


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
