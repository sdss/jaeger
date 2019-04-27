#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-04
# @Filename: core.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-26 23:23:24

import asyncio

import can.interfaces.virtual
import can.notifier
import numpy

import jaeger
import jaeger.commands
from jaeger.commands import Message
from jaeger.maskbits import PositionerStatus
from jaeger.positioner import VirtualPositioner
from jaeger.utils import int_to_bytes, parse_identifier


__ALL__ = ['VirtualFPS']


class VirtualFPS(jaeger.BaseFPS):
    """A mock Focal Plane System for testing and development.

    This class listens to the
    `python-can <https://python-can.readthedocs.io/en/stable/>`__
    virtual bus and responds as if real positioners were plugged into the
    system.

    Parameters
    ----------
    channel : str
        The channel of the virtual bus to listen to.
    layout : str
        The layout describing the position of the robots on the focal plane.
        If `None`, the default layout will be used. Can be either a layout name
        to be recovered from the database, or a file path to the layout
        configuration.
    positions : dict
        A dictionary of positioner ID and ``(alpha, beta)`` initial positions.
        Omitted positioner will be initialised folded at ``(0, 180)`` degrees.
    loop
        The event loop, or the current event loop will be used.

    """

    def __init__(self, channel, layout=None, positions=None, loop=None):

        #: The virtual bus.
        self.bus = can.interfaces.virtual.VirtualBus(channel)

        self.loop = loop if loop is not None else asyncio.get_event_loop()

        #: A `.JaegerReaderCallback` instance that calls a callback when
        #: a new message is received from the bus.
        self.listener = jaeger.JaegerReaderCallback(self.process_message, loop=self.loop)

        #: A `~.can.Notifier` instance that processes messages from
        #: the bus asynchronously.
        self.notifier = can.notifier.Notifier(self.bus, [self.listener], loop=self.loop)

        super().__init__(layout=layout, positioner_class=VirtualPositioner)

        self.initialise(positions)

    def initialise(self, positions=None):
        """Sets the initial states of the positioners."""

        initial_status = (PositionerStatus.SYSTEM_INITIALIZATION |
                          PositionerStatus.DATUM_ALPHA_INITIALIZED |
                          PositionerStatus.DATUM_BETA_INITIALIZED |
                          PositionerStatus.DATUM_INITIALIZED |
                          PositionerStatus.POSITION_RESTORED)

        for positioner in self.positioners.values():
            positioner.firmware = '99.99.99'
            positioner.status = initial_status

        self.set_positions(positions)

    def set_positions(self, positions=None):
        """Sets the alpha/beta positions of robots.

        Parameters
        ----------
        positions : dict
            A dictionary of positioner ID and ``(alpha, beta)`` initial
            positions. Omitted positioner will be initialised folded at
            ``(0, 180)`` degrees.

        """

        for positioner_id in self.positioners:

            if positions and positioner_id in positions:
                alpha, beta = positions[positioner_id]
            else:
                alpha, beta = 0, 180

            self.positioners[positioner_id].alpha = alpha
            self.positioners[positioner_id].beta = beta

    def process_message(self, message):
        """Processes a message from the virtual bus."""

        pid, cid, uid, __ = parse_identifier(message.arbitration_id)

        positioner_ids = [pid] if pid != 0 else list(self.positioners)

        command = jaeger.CommandID(cid).get_command()

        for pid in positioner_ids:

            if cid == jaeger.CommandID.GET_STATUS:
                data, response_code = self.get_status(pid)
            elif cid == jaeger.CommandID.GET_FIRMWARE_VERSION:
                data, response_code = self.get_firmware_version(pid)
            else:
                data = []
                response_code = 0

            message = Message(command, positioner_id=pid, uid=uid,
                              response_code=response_code, data=data)

            self.bus.send(message)

        return

    def get_status(self, positioner_id):
        """Replies to GET_STATUS."""

        status = self.positioners[positioner_id].status
        data = int_to_bytes(status)
        response_code = 0

        return data, response_code

    def get_firmware_version(self, positioner_id):
        """Replies to GET_FIRMWARE_VERSION."""

        firmware = self.positioners[positioner_id].firmware
        if firmware is None:
            firmware = '00.00.00'

        data = bytearray()
        for chunk in firmware.split('.'):
            data += int_to_bytes(int(chunk), dtype=numpy.uint8)

        response_code = 0

        return data, response_code
