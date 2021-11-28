#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: Ricardo Araujo
# @Filename: cannet.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import time

from .bus import BusABC
from .message import Message


class CANNetMessage(Message):
    __slots__ = ("interface", "bus")


class CANNetBus(BusABC):
    r"""Interface for Ixxat CAN\@net NT 200/420.

    Parameters
    ----------
    channel : str
        The IP address of the remote device (e.g. ``192.168.1.1``, ...).
    port : int
        The port of the device.
    bitrate : int
        Bitrate in bit/s.
    buses : list
        The buses to open in the device. Messages that do not specify a
        bus will be sent to all the open buses.
    timeout : float
        Timeout for connection.

    """

    # the supported bitrates and their commands
    _BITRATES = {
        5000: "5",
        10000: "10",
        20000: "20",
        50000: "50",
        62500: "62.5",
        83300: "83.3",
        100000: "100",
        125000: "125",
        500000: "500",
        800000: "800",
        1000000: "1000",
    }

    _REMOTE_PORT = 19228

    LINE_TERMINATOR = b"\n"

    def __init__(
        self,
        channel,
        port=None,
        bitrate=None,
        buses=[1],
        timeout=5,
        **kwargs,
    ):

        if not channel:  # if None or empty
            raise TypeError("Must specify a TCP address.")

        self.channel = channel

        if not bitrate:
            raise TypeError("Must specify a bitrate.")

        self.port = port or self._REMOTE_PORT

        self.bitrate = bitrate
        self.buses = buses

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.connected = False

        self._timeout = timeout

        self.channel_info = f"CAN@net channel={channel!r}, buses={self.buses!r}"

        super(CANNetBus, self).__init__(channel, bitrate=None, **kwargs)

    def write(self, string):

        if not self.connected or not self.writer:
            raise ConnectionError(f"Interface {self.channel} is not connected.")

        self.writer.write(string.encode() + self.LINE_TERMINATOR)

    def _write_to_buses(self, string, buses=None):
        """Writes a string to the correct bus."""

        if not buses:
            buses = self.buses
        elif not isinstance(buses, (list, tuple)):
            buses = [buses]

        for bus in buses:
            self.write(string.format(bus=bus))

    async def _open_internal(self, timeout=None):

        timeout = timeout or self._timeout

        self.close()

        try:
            open_conn = asyncio.open_connection(self.channel, self.port)
            self.reader, self.writer = await asyncio.wait_for(open_conn, timeout)
        except asyncio.TimeoutError:
            self.connected = False
            return False

        self.connected = True

        if self.bitrate in self._BITRATES:
            self._write_to_buses("CAN {bus} STOP")
            self._write_to_buses(f"CAN {{bus}} INIT STD {self._BITRATES[self.bitrate]}")
            self._write_to_buses("CAN {bus} FILTER CLEAR")
            self._write_to_buses("CAN {bus} FILTER ADD EXT 00000000 00000000")
        else:
            bitrates = ", ".join(map(str, self._BITRATES))
            raise ValueError(f"Invalid bitrate, choose one of {bitrates}.")

        self._write_to_buses("CAN {bus} START")

        # await self.writer.drain()

        # Clear buffer
        await self.reader.read(8192)

        return True

    def close(self, buses=None):

        if self.writer and not self.writer.is_closing():
            self._write_to_buses("CAN {bus} STOP")
            self.writer.close()

        self.connected = False
        self.writer = self.reader = None

    async def get(self):

        canId = None
        remote = False
        extended = False
        frame = []

        if not self.reader:
            raise ConnectionError(f"Interface {self.channel} is not connected.")

        msgStr = await self.reader.readuntil(self.LINE_TERMINATOR)

        readStr = msgStr.strip(self.LINE_TERMINATOR).decode()
        if not readStr:
            return None

        # Message is M 1 CSD 100 55 AA 55 AA or M 2 CED 18FE0201 01 02 03 04 05 06 07 08
        # Check if we have a message from the CAN network. Otherwise this is a message
        # from the device so we return it.
        data = readStr.split(" ")
        if data[0] != "M":
            msg = CANNetMessage(
                arbitration_id=0,
                timestamp=time.time(),
                dlc=0,
                data=msgStr,
            )
            msg.interface = self
            msg.bus = None
            return msg

        # check if it is the proper CAN bus
        bus = int(data[1])
        if bus not in self.buses:
            return None

        # check if standard packet, FD not supported
        if data[2][0] != "C":
            return None

        # check if remote frame
        if data[2][2] == "D":
            remote = False
        elif data[2][2] == "R":
            remote = True

        # check if standard or extended packet
        if data[2][1] == "S":
            extended = False
        elif data[2][1] == "E":
            extended = True
        else:
            return None

        # get canId
        canId = int(data[3], 16)

        # get frame data
        dlc = 0
        for byte in data[4:]:
            frame.append(int(byte, 16))
            dlc = dlc + 1

        if canId is not None:
            msg = CANNetMessage(
                arbitration_id=canId,
                is_extended_id=extended,
                timestamp=time.time(),
                is_remote_frame=remote,
                dlc=dlc,
                data=frame,
            )
            msg.interface = self
            msg.bus = bus
            return msg

        return None

    def send(self, msg, bus=None):

        buses = bus or self.buses
        if not isinstance(buses, (list, tuple)):
            buses = [buses]

        for bus in buses:

            sendStr = f"M {bus} "

            if msg.is_extended_id:
                if msg.is_remote_frame:
                    sendStr += f"CER {msg.arbitration_id:08X}"
                else:
                    sendStr += f"CED {msg.arbitration_id:08X}"
            else:
                if msg.is_remote_frame:
                    sendStr += f"CSR {msg.arbitration_id:03X}"
                else:
                    sendStr += f"CSD {msg.arbitration_id:03X}"

            sendStr += "".join([" %02X" % b for b in msg.data])

            self.write(sendStr)

    def shutdown(self):
        self.close()
