#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: testing.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import zlib

from typing import Optional, Tuple

import jaeger
from jaeger import config, utils
from jaeger.commands import CommandID
from jaeger.interfaces import Message, VirtualBus
from jaeger.maskbits import BootloaderStatus, PositionerStatus, ResponseCode
from jaeger.utils.helpers import StatusMixIn


__all__ = ["VirtualFPS", "VirtualPositioner"]


TIME_STEP = config["positioner"]["time_step"]


class VirtualFPS(jaeger.FPS):
    """A mock Focal Plane System for testing and development.

    This class listens to a virtual bus and responds as if real positioners were
    plugged into the system.

    """

    def __post_init__(self):

        self.can = "virtual"
        self.ieb = True

        super().__post_init__()

        self._vpositioner_bus = VirtualBus(config["profiles"]["virtual"]["channel"])
        self._vpositioners = {}

        asyncio.create_task(self.process_messages())

    def add_virtual_positioner(self, pid: int):

        self._vpositioners[pid] = VirtualPositioner(pid, bus=self._vpositioner_bus)

    async def process_messages(self):

        while True:

            msg = await self._vpositioner_bus.get()

            if msg is None:
                continue

            arbitration_id = msg.arbitration_id
            positioner_id, command_id, uid, __ = utils.parse_identifier(arbitration_id)

            if positioner_id != 0 and positioner_id not in self._vpositioners:
                continue

            if positioner_id == 0:
                await asyncio.gather(
                    *[
                        vp.process_message(msg, positioner_id, command_id, uid)
                        for vp in self._vpositioners.values()
                    ]
                )
            else:
                vp = self._vpositioners[positioner_id]
                await vp.process_message(msg, positioner_id, command_id, uid)


class VirtualPositioner(StatusMixIn):
    """A virtual positioner that listen to CAN commands.

    An object of `.VirtualPositioner` represents a real positioner firmware.
    It knows it's own state at any time and replies.

    Parameters
    ----------

    positioner_id
        The ID of the positioner.
    centre
        A tuple of two floats indicating where on the focal plane the
        positioner is located.
    position
        A tuple of two floats indicating the angles of the alpha and beta arms.
    speed
        A tuple of two float indicating the RPM on the input for alpha and
        beta.
    firmware
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
        positioner_id: int,
        bus: Optional[VirtualBus] = None,
        centre: Optional[tuple] = None,
        position: Tuple[float, float] = (0.0, 0.0),
        speed: Optional[tuple] = None,
        firmware: str = "10.11.12",
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

        self.number_trajectories = 1

        self.bus = bus

        StatusMixIn.__init__(
            self,
            PositionerStatus,
            initial_status=self._initial_status,
        )

    async def process_message(self, msg, positioner_id, command_id, uid):
        """Processes incoming commands from the bus."""

        command_id = CommandID(command_id)
        command = command_id.get_command_class()

        if positioner_id == 0 and not command.broadcastable:
            self.reply(
                command_id,
                uid,
                response_code=ResponseCode.INVALID_BROADCAST_COMMAND,
            )
            return

        if command_id == CommandID.GET_ID:
            self.reply(command_id, uid)

        elif command_id == CommandID.GET_FIRMWARE_VERSION:
            data_firmware = command.encode(self.firmware)  # type: ignore
            self.reply(command_id, uid, data=data_firmware)

        elif command_id == CommandID.GET_STATUS:
            data_status = utils.int_to_bytes(self.status)
            self.reply(command_id, uid, data=data_status)

        elif command_id == CommandID.GET_NUMBER_TRAJECTORIES:
            data_status = utils.int_to_bytes(self.number_trajectories)
            self.reply(command_id, uid, data=data_status)

        elif command_id in [
            CommandID.GO_TO_ABSOLUTE_POSITION,
            CommandID.GO_TO_RELATIVE_POSITION,
        ]:
            asyncio.create_task(self.process_goto(msg))

        elif command_id == CommandID.GET_ACTUAL_POSITION:
            data_position = command.encode(*self.position)  # type: ignore
            self.reply(command_id, uid, data=data_position)

        elif command_id == CommandID.SET_SPEED:
            data_speed = command.encode(*self.speed)  # type: ignore
            self.reply(command_id, uid, data=data_speed)

        elif command_id == CommandID.START_FIRMWARE_UPGRADE:
            if not self.is_bootloader():
                self.reply(
                    command_id,
                    uid,
                    response_code=ResponseCode.INVALID_COMMAND,
                )
                return

            try:
                data = msg.data
                firmware_size = utils.bytes_to_int(data[0:4], "u4")
                crc32 = utils.bytes_to_int(data[4:9], "u4")
            except Exception:
                self.reply(
                    command_id,
                    uid,
                    response_code=ResponseCode.INVALID_COMMAND,
                )
                return

            self._firmware_size = firmware_size
            self._crc32 = crc32
            self._firmware_received = b""

            self.reply(command_id, uid)

        elif command_id == CommandID.SEND_FIRMWARE_DATA:
            asyncio.create_task(self.process_firmware_data(uid, msg.data))

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
            self.positioner_id,
            command_id,
            uid=uid,
            response_code=response_code,
        )

        for data_chunk in data:
            message = Message(
                arbitration_id=reply_id,
                is_extended_id=True,
                data=data_chunk,
            )
            # if self.notifier:
            #     self.notifier.bus.send(message)
            assert self.bus
            self.bus.send(message)

    async def process_firmware_data(self, uid, data):
        """Processes ``SEND_FIRMWARE_DATA`` commands."""

        command_id = CommandID.SEND_FIRMWARE_DATA

        if len(data) > 8:
            self.reply(
                command_id,
                uid,
                response_code=ResponseCode.VALUE_OUT_OF_RANGE,
            )
            return

        self._firmware_received += data

        fw_size = len(self._firmware_received)

        if fw_size > self._firmware_size:
            self.reply(
                command_id,
                uid,
                response_code=ResponseCode.VALUE_OUT_OF_RANGE,
            )
        elif fw_size == self._firmware_size:
            if not zlib.crc32(self._firmware_received) == self._crc32:
                self.reply(
                    command_id,
                    uid,
                    response_code=ResponseCode.VALUE_OUT_OF_RANGE,
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
        alpha_move, beta_move = command.decode(data)  # type: ignore

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

        self.position = (target_alpha, target_beta)

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
