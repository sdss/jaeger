#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: testing.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import zlib
from contextlib import suppress

from can import Message
from can.interfaces.virtual import VirtualBus
from can.listener import AsyncBufferedReader

import jaeger
from jaeger import config, utils
from jaeger.commands import CommandID
from jaeger.maskbits import BootloaderStatus, PositionerStatus, ResponseCode
from jaeger.utils.helpers import StatusMixIn


__all__ = ["VirtualFPS", "VirtualPositioner"]


TIME_STEP = config["positioner"]["time_step"]


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
    loop : event loop or `None`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.
    engineering_mode : bool
        If `True`, disables most safety checks to enable debugging. This may
        result in hardware damage so it must not be used lightly.

    """

    def __init__(self, layout=None, **kwargs):

        super().__init__(can_profile="virtual", layout=layout, ieb=True)


class VirtualPositioner(StatusMixIn):
    """A virtual positioner that listen to CAN commands.

    An object of `.VirtualPositioner` represents a real positioner firmware.
    It knows it's own state at any time and replies.

    Parameters
    ----------

    positioner_id : int
        The ID of the positioner.
    centre : tuple
        A tuple of two floats indicating where on the focal plane the
        positioner is located.
    position : tuple
        A tuple of two floats indicating the angles of the alpha and beta arms.
    speed : tuple
        A tuple of two float indicating the RPM on the input for alpha and
        beta.
    channel : str
        The channel on which to listen to the virtual CAN bus. Defaults to
        ``config['profiles']['virtual']['channel']``.
    loop
        The event loop.
    notifier : ~can.Notifier
        The `python-can <https://python-can.readthedocs.io/en/stable/>`_
        `~can.Notifier` instance that informs of new messages in the bus.
    firmware : str
        The firmware version, as a string with the format `AB.CD.EF`.

    """

    #: The initial status of the positioner. Represents a positioner that
    #: is not moving and is fully calibrated.
    _initial_status = (
        PositionerStatus.SYSTEM_INITIALIZED
        | PositionerStatus.DISPLACEMENT_COMPLETED
        | PositionerStatus.DISPLACEMENT_COMPLETED_ALPHA
        | PositionerStatus.DISPLACEMENT_COMPLETED_BETA
        | PositionerStatus.POSITION_RESTORED
        | PositionerStatus.DATUM_ALPHA_INITIALIZED
        | PositionerStatus.DATUM_BETA_INITIALIZED
        | PositionerStatus.MOTOR_ALPHA_CALIBRATED
        | PositionerStatus.MOTOR_BETA_CALIBRATED
        | PositionerStatus.CLOSED_LOOP_ALPHA
        | PositionerStatus.CLOSED_LOOP_BETA
    )

    def __init__(
        self,
        positioner_id,
        centre=None,
        position=(0.0, 0.0),
        speed=None,
        channel=None,
        loop=None,
        notifier=None,
        firmware="10.11.12",
    ):

        self.positioner_id = positioner_id
        self.centre = centre or (None, None)

        self.position = position
        self.speed = speed or (
            config["positioner"]["motor_speed"],
            config["positioner"]["motor_speed"],
        )

        self.firmware = firmware
        self._initial_firmware = firmware

        # To be used for a firmware upgrade.
        self._crc32 = 0
        self._firmware_size = 0
        self._firmware_received = b""

        self.channel = channel or config["profiles"]["virtual"]["channel"]
        self.interface = VirtualBus(self.channel)

        self.loop = loop or asyncio.get_event_loop()

        self.notifier = notifier
        self.listener = AsyncBufferedReader(loop=self.loop)
        self._listener_task = None

        if self.notifier:
            self.notifier.add_listener(self.listener)
            self._listener_task = self.loop.create_task(self.process_message())

        StatusMixIn.__init__(
            self, PositionerStatus, initial_status=self._initial_status
        )

    async def process_message(self):
        """Processes incoming commands from the bus."""

        while True:

            msg = await self.listener.get_message()

            arbitration_id = msg.arbitration_id
            positioner_id, command_id, uid, __ = utils.parse_identifier(arbitration_id)

            if positioner_id not in [0, self.positioner_id]:
                continue

            command_id = CommandID(command_id)
            command = command_id.get_command_class()

            if positioner_id == 0 and not command.broadcastable:
                self.reply(
                    command_id,
                    uid,
                    response_code=ResponseCode.INVALID_BROADCAST_COMMAND,
                )
                continue

            if command_id == CommandID.GET_ID:
                self.reply(command_id, uid)

            elif command_id == CommandID.GET_FIRMWARE_VERSION:
                data_firmware = command.encode(self.firmware)
                self.reply(command_id, uid, data=data_firmware)

            elif command_id == CommandID.GET_STATUS:
                data_status = utils.int_to_bytes(self.status)
                self.reply(command_id, uid, data=data_status)

            elif command_id in [
                CommandID.GO_TO_ABSOLUTE_POSITION,
                CommandID.GO_TO_RELATIVE_POSITION,
            ]:
                self.loop.create_task(self.process_goto(msg))

            elif command_id == CommandID.GET_ACTUAL_POSITION:
                data_position = command.encode(*self.position)
                self.reply(command_id, uid, data=data_position)

            elif command_id == CommandID.SET_SPEED:
                data_speed = command.encode(*self.speed)
                self.reply(command_id, uid, data=data_speed)

            elif command_id == CommandID.START_FIRMWARE_UPGRADE:
                if not self.is_bootloader():
                    self.reply(
                        command_id, uid, response_code=ResponseCode.INVALID_COMMAND
                    )
                    continue

                try:
                    data = msg.data
                    firmware_size = utils.bytes_to_int(data[0:4], "u4")
                    crc32 = utils.bytes_to_int(data[4:9], "u4")
                except Exception:
                    self.reply(
                        command_id, uid, response_code=ResponseCode.INVALID_COMMAND
                    )
                    continue

                self._firmware_size = firmware_size
                self._crc32 = crc32
                self._firmware_received = b""

                self.reply(command_id, uid)

            elif command_id == CommandID.SEND_FIRMWARE_DATA:
                self.process_firmware_data(uid, msg.data)

            else:
                # Should be a valid command or CommandID(command_id) would
                # have failed. Just return OK.
                self.reply(command_id, uid)

    def reply(self, command_id, uid, response_code=None, data=None):

        response_code = response_code or ResponseCode.COMMAND_ACCEPTED

        if isinstance(data, (bytearray, bytes)):
            data = [data]
        elif not data:
            data = [None]

        reply_id = utils.get_identifier(
            self.positioner_id, command_id, uid=uid, response_code=response_code
        )

        for data_chunk in data:
            message = Message(
                arbitration_id=reply_id, is_extended_id=True, data=data_chunk
            )
            self.notifier.bus.send(message)

    def process_firmware_data(self, uid, data):
        """Processes ``SEND_FIRMWARE_DATA`` commands."""

        command_id = CommandID.SEND_FIRMWARE_DATA

        if len(data) > 8:
            self.reply(command_id, uid, response_code=ResponseCode.VALUE_OUT_OF_RANGE)
            return

        self._firmware_received += data

        fw_size = len(self._firmware_received)

        if fw_size > self._firmware_size:
            self.reply(command_id, uid, response_code=ResponseCode.VALUE_OUT_OF_RANGE)
        elif fw_size == self._firmware_size:
            if not zlib.crc32(self._firmware_received) == self._crc32:
                self.reply(
                    command_id, uid, response_code=ResponseCode.VALUE_OUT_OF_RANGE
                )
            else:
                self.firmware = self._firmware_received.decode("utf-8")[-8:]
                self.reply(command_id, uid)
        else:
            self.reply(command_id, uid)

    async def process_goto(self, message):
        """Process an absolute or relative goto command."""

        __, command_id, uid, __ = utils.parse_identifier(message.arbitration_id)
        command_id = CommandID(command_id)
        command = command_id.get_command_class()

        data = message.data
        alpha_move, beta_move = command.decode(data)

        if command_id == CommandID.GO_TO_RELATIVE_POSITION:
            alpha_move += self.position[0]
            beta_move += self.position[0]

        target_alpha = self.position[0] + alpha_move
        target_beta = self.position[1] + beta_move

        if (
            target_alpha < 0
            or target_beta < 0
            or target_alpha > 360
            or target_beta > 360
        ):
            self.reply(command_id, uid, ResponseCode.VALUE_OUT_OF_RANGE)
            return

        if alpha_move == 0.0:
            alpha_move_time = 0.0
        else:
            alpha_move_time = int(
                utils.get_goto_move_time(alpha_move, self.speed[0]) / TIME_STEP
            )

        if beta_move == 0.0:
            beta_move_time = 0.0
        else:
            beta_move_time = int(
                utils.get_goto_move_time(beta_move, self.speed[1]) / TIME_STEP
            )

        self.reply(
            command_id,
            uid,
            ResponseCode.COMMAND_ACCEPTED,
            data=[
                utils.int_to_bytes(alpha_move_time, "i4")
                + utils.int_to_bytes(beta_move_time, "i4")
            ],
        )

        self.status ^= (
            PositionerStatus.DISPLACEMENT_COMPLETED
            | PositionerStatus.DISPLACEMENT_COMPLETED_ALPHA
            | PositionerStatus.DISPLACEMENT_COMPLETED_BETA
        )
        self.status |= (
            PositionerStatus.TRAJECTORY_ALPHA_RECEIVED
            | PositionerStatus.TRAJECTORY_BETA_RECEIVED
        )

        await asyncio.sleep(max(alpha_move * TIME_STEP, beta_move_time * TIME_STEP))

        self.status |= (
            PositionerStatus.DISPLACEMENT_COMPLETED
            | PositionerStatus.DISPLACEMENT_COMPLETED_ALPHA
            | PositionerStatus.DISPLACEMENT_COMPLETED_BETA
        )

    def reset(self):
        """Resets the positioner."""

        self.position = (0.0, 0.0)
        self.status = self._initial_status
        self.firmware = self._initial_firmware

    def is_bootloader(self):
        """Returns `True` if the positioner is in bootloader mode."""

        return self.firmware.split(".")[1] == "80"

    def set_bootloader(self, bootloader=True):
        """Sets the positioner in bootloader mode."""

        firmware_chunks = self.firmware.split(".")

        if bootloader:
            firmware_chunks[1] = "80"
            self.flags = BootloaderStatus
            self.status = BootloaderStatus.BOOTLOADER_INIT
        else:
            firmware_chunks[1] = self._initial_firmware.split(".")[1]
            self.flags = PositionerStatus
            self.status = self._initial_status

        self.firmware = ".".join(firmware_chunks)

    async def shutdown(self):
        """Stops the command queue."""

        if self._listener_task:
            self._listener_task.cancel()

            with suppress(asyncio.CancelledError):
                await self._listener_task
