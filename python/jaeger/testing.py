#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: testing.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
from contextlib import suppress

from can import Message
from can.interfaces.virtual import VirtualBus
from can.listener import AsyncBufferedReader

import jaeger
from jaeger import utils
from jaeger.commands import CommandID
from jaeger.maskbits import BootloaderStatus
from jaeger.maskbits import PositionerStatusV4_1 as PS
from jaeger.maskbits import ResponseCode
from jaeger.utils.helpers import StatusMixIn


__ALL__ = ['VirtualFPS', 'VirtualPositioner']


class VirtualFPS(jaeger.FPS):
    """A mock Focal Plane System for testing and development.

    This class listens to the
    `python-can <https://python-can.readthedocs.io/en/stable/>`__
    virtual bus and responds as if real positioners were plugged into the
    system.

    Parameters
    ----------
    layout : str
        The layout describing the position of the robots on the focal plane.
        If `None`, the default layout will be used. Can be either a layout name
        to be recovered from the database, or a file path to the layout
        configuration.
    qa : bool or path
        A path to the database used to store QA information. If `True`, uses
        the value from ``config.files.qa_database``. If `False`, does not do
        any QA recording.
    loop : event loop or `None`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.
    engineering_mode : bool
        If `True`, disables most safety checks to enable debugging. This may
        result in hardware damage so it must not be used lightly.

    """

    def __init__(self, layout=None, qa=False, loop=None, engineering_mode=False):

        super().__init__(can_profile='virtual', layout=layout,
                         wago=False, qa=qa)


class VirtualPositioner(StatusMixIn):
    """A virtual positioner that listen to CAN commands.

    An object of `.VirtualPositioner` represents a real positioner firmware.
    It knows it's own state at any time and replies

    """

    _initial_status = (PS.SYSTEM_INITIALIZED |
                       PS.DISPLACEMENT_COMPLETED |
                       PS.DISPLACEMENT_COMPLETED_ALPHA |
                       PS.DISPLACEMENT_COMPLETED_BETA |
                       PS.POSITION_RESTORED |
                       PS.DATUM_ALPHA_INITIALIZED |
                       PS.DATUM_BETA_INITIALIZED |
                       PS.MOTOR_ALPHA_CALIBRATED |
                       PS.MOTOR_BETA_CALIBRATED)

    _initial_firmware = '10.11.12'

    def __init__(self, positioner_id, centre=None, position=(0.0, 0.0),
                 speed=None, channel=None, loop=None, notifier=None):

        self.positioner_id = positioner_id
        self.centre = centre or (None, None)

        self.position = position
        self.speed = speed or (jaeger.config['positioner']['motor_speed'],
                               jaeger.config['positioner']['motor_speed'])

        self.firmware = self._initial_firmware

        self.channel = channel or jaeger.config['profiles']['virtual']['channel']
        self.interface = VirtualBus(self.channel)

        self.loop = loop or asyncio.get_event_loop()

        self.notifier = notifier
        self.listener = AsyncBufferedReader(loop=self.loop)
        self._listener_task = None

        if self.notifier:
            self.notifier.add_listener(self.listener)
            self._listener_task = self.loop.create_task(self.process_message())

        StatusMixIn.__init__(self, PS, initial_status=self._initial_status)

    async def process_message(self):
        """Processes incoming commands from the bus."""

        while True:

            msg = await self.listener.get_message()

            arbitration_id = msg.arbitration_id
            positioner_id, command_id, uid, __ = utils.parse_identifier(arbitration_id)

            if positioner_id not in [0, self.positioner_id]:
                continue

            command_id = CommandID(command_id)
            command = command_id.get_command()

            if command_id == CommandID.GET_ID:
                self.reply(command_id, uid)

            elif command_id == CommandID.GET_STATUS:
                data_status = utils.int_to_bytes(self.status)
                self.reply(command_id, uid, data=data_status)

            elif command_id == CommandID.GET_FIRMWARE_VERSION:
                data_firmware = command.encode(self.firmware)
                self.reply(command_id, uid, data=data_firmware)

            elif command_id == CommandID.GET_ACTUAL_POSITION:
                data_position = command.encode(*self.position)
                self.reply(command_id, uid, data=data_position)

            elif command_id == CommandID.SET_SPEED:
                data_speed = command.encode(*self.speed)
                self.reply(command_id, uid, data=data_speed)

    def reply(self, command_id, uid, response_code=None, data=None):

        response_code = response_code or ResponseCode.COMMAND_ACCEPTED

        if isinstance(data, (bytearray, bytes)):
            data = [data]
        elif not data:
            data = [None]

        reply_id = utils.get_identifier(self.positioner_id,
                                        command_id,
                                        uid=uid,
                                        response_code=response_code)

        for data_chunk in data:
            message = Message(arbitration_id=reply_id,
                              extended_id=True,
                              data=data_chunk)
            self.notifier.bus.send(message)

    def reset(self):
        """Resets the positioner."""

        self.position = (0.0, 0.0)
        self.status = self._initial_status
        self.firmware = self._initial_firmware

    def set_bootloader(self, bootloader=True):
        """Sets the positioner in bootloader mode."""

        if bootloader:
            self.firmware = '10.80.12'
            self.flag = BootloaderStatus
            self.status = BootloaderStatus.BOOTLOADER_INIT
        else:
            self.firmware = self._initial_firmware
            self.flag = PS
            self.status = self._initial_status

    async def shutdown(self):
        """Stops the command queue."""

        if self._listener_task:
            self._listener_task.cancel()

            with suppress(asyncio.CancelledError):
                await self._listener_task
