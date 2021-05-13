#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: Ricardo Araujo
# @Filename: cannet.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import socket
import time

from can import BusABC, Message


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

        if not bitrate:
            raise TypeError("Must specify a bitrate.")

        port = port or self._REMOTE_PORT

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._serverAddress = (channel, port)

        self._socket.settimeout(timeout)
        self._socket.connect(self._serverAddress)
        self._socket.settimeout(None)

        self._buffer = bytearray()

        self.bitrate = bitrate
        self.buses = buses

        self.open()

        self.channel_info = f"CAN@net channel={channel!r}, buses={self.buses!r}"

        super(CANNetBus, self).__init__(channel, bitrate=None, **kwargs)

    def write(self, string):

        self._socket.send(string.encode() + self.LINE_TERMINATOR)

    def _write_to_buses(self, string, buses=None):
        """Writes a string to the correct bus."""

        if not buses:
            buses = self.buses
        elif not isinstance(buses, (list, tuple)):
            buses = [buses]

        for bus in buses:
            self.write(string.format(bus=bus))

    def open(self):

        self.close()

        if self.bitrate in self._BITRATES:
            self._write_to_buses(
                "CAN {bus} " + f"INIT STD {self._BITRATES[self.bitrate]}"
            )
            self._write_to_buses("CAN {bus} " + "FILTER CLEAR")
            self._write_to_buses("CAN {bus} " + "FILTER ADD EXT 00000000 00000000")
        else:
            raise ValueError(
                "Invalid bitrate, choose one of "
                + (", ".join(map(str, self._BITRATES)))
                + "."
            )

        self._write_to_buses("CAN {bus} START")

        # Clear buffer
        self._socket.recv(8192)

    def close(self, buses=None):

        self._write_to_buses("CAN {bus} STOP", buses=buses)

    def _recv_internal(self, timeout):

        if timeout != self._socket.gettimeout():
            self._socket.settimeout(timeout)

        canId = None
        remote = False
        extended = False
        frame = []

        # Check that we don't have already a message
        while self.LINE_TERMINATOR not in self._buffer:
            self._buffer += self._socket.recv(1)

        if self.LINE_TERMINATOR not in self._buffer:
            # Timed out
            return None, False

        msgStr, _, self._buffer = self._buffer.partition(self.LINE_TERMINATOR)

        readStr = msgStr.decode()
        if not readStr:
            return None, False

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
            return msg, False

        # check if it is the proper CAN bus
        bus = int(data[1])
        if bus not in self.buses:
            return None, False

        # check if standard packet, FD not supported
        if data[2][0] != "C":
            return None, False

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
            return None, False

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
            return msg, False

        return None, False

    def send(self, msg, bus=None, timeout=None):

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
        self._socket.close()

    def fileno(self):
        if hasattr(self._socket, "fileno"):
            return self._socket.fileno()
        return -1
