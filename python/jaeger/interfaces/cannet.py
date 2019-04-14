#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: Ricardo Araujo
# @Filename: cannet.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-14 18:33:55

import time
import logging
import socket

from can import BusABC, Message

logger = logging.getLogger(__name__)


class CANNetBus(BusABC):
    """Interface for ixxat can@Net NT 200 compatible interfaces (win32/linux).

    Only one CAN channel supported at the moment (NT200 has 2 channels and
    NT420 has 4 channels)

    """

    # the supported bitrates and their commands
    _BITRATES = {
        5000: '5',
        10000: '10',
        20000: '20',
        50000: '50',
        62500: '62.5',
        83300: '83.3',
        100000: '100',
        125000: '125',
        500000: '500',
        800000: '800',
        1000000: '1000'
    }

    _REMOTE_PORT = 19228

    LINE_TERMINATOR = b'\n'

    def __init__(self, ip, channel=1, bitrate=None, **kwargs):
        """
        ip : str
            IP address of remote device (e.g. ``192.168.1.1``, ...).
        channel : int
            The channel of the device to use.
        bitrate:
            Bitrate in bit/s
        """

        if not ip:  # if None or empty
            raise TypeError('Must specify a TCP address.')

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._serverAddress = (ip, self._REMOTE_PORT)

        # self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        self._socket.connect(self._serverAddress)

        # self._socket.setblocking(False)

        self._buffer = bytearray()

        self._channel = channel    # TODO only using channel 1 so far
        if bitrate is not None:
            self.close()
            if bitrate in self._BITRATES:
                self.write(f'CAN {self._channel} INIT STD {self._BITRATES[bitrate]}')
                print(self._socket.recv(64))
                self.write(f'CAN {self._channel} FILTER CLEAR')
                print(self._socket.recv(64))
                self.write(f'CAN {self._channel} FILTER ADD EXT 00000000 00000000')
                print(self._socket.recv(64))
            else:
                raise ValueError('Invalid bitrate, choose one of ' +
                                 (', '.join(self._BITRATES)) + '.')

        self.open()

        super(CANNetBus, self).__init__(ip, bitrate=None, **kwargs)

    def write(self, string):
        self._socket.send(string.encode() + self.LINE_TERMINATOR)

    def open(self):
        self.write(f'CAN {self._channel} START')
        print(self._socket.recv(64))

    def close(self):
        self.write(f'CAN {self._channel} STOP')
        print(self._socket.recv(64))

    def _recv_internal(self, timeout):
        # if timeout != self._socket.gettimeout():
        #    self._socket.settimeout(timeout)

        canId = None
        remote = False
        extended = False
        frame = []

        # Check that we don't have already a message
        while (self.LINE_TERMINATOR not in self._buffer):
            self._buffer += self._socket.recv(1)

        if self.LINE_TERMINATOR not in self._buffer:
            # Timed out
            return None, False

        msgStr, _, self._buffer = self._buffer.partition(self.LINE_TERMINATOR)
        # print('message: ' + msgStr.decode())
        # print('still in buffer: ' + self._buffer.decode())

        readStr = msgStr.decode()
        if not readStr:
            return None, False

        # message is M 1 CSD 100 55 AA 55 AA or M 2 CED 18FE0201 01 02 03 04 05 06 07 08
        # check if we have a message
        data = readStr.split(' ')
        if data[0] != 'M':
            # we don't have a message
            return None, False

        # check if it is the proper CAN bus (TODO handle multiple CAN buses)
        if int(data[1]) != self._channel:
            return None, False

        # check if standard packet, FD not supported
        if data[2][0] != 'C':
            return None, False

        # check if remote frame
        if data[2][2] == 'D':
            remote = False
        elif data[2][2] == 'R':
            remote = True

        # check if standard or extended packet
        if data[2][1] == 'S':
            extended = False
        elif data[2][1] == 'E':
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
            msg = Message(arbitration_id=canId,
                          is_extended_id=extended,
                          timestamp=time.time(),   # Better than nothing...
                          is_remote_frame=remote,
                          dlc=dlc,
                          data=frame)
            # print(msg)
            return msg, False
        return None, False

    def send(self, msg, timeout=None):
        # if timeout != self._socket.gettimeout():
        #     self._socket.settimeout(timeout)

        if msg.is_extended_id:
            if msg.is_remote_frame:
                sendStr = f'M {self._channel} CER {msg.arbitration_id:08X}'
            else:
                sendStr = f'M {self._channel} CED {msg.arbitration_id:08X}'
        else:
            if msg.is_remote_frame:
                sendStr = f'M {self._channel} CSR {msg.arbitration_id:03X}'
            else:
                sendStr = f'M {self._channel} CSD {msg.arbitration_id:03X}'

        sendStr += ''.join([' %02X' % b for b in msg.data])

        self.write(sendStr)

    def shutdown(self):
        self.close()
        self._socket.close()

    def fileno(self):
        if hasattr(self._socket, 'fileno'):
            return self._socket.fileno()
        return -1
