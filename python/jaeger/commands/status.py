#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from typing import Dict, List

import numpy

from jaeger.commands import Command, CommandID
from jaeger.utils import bytes_to_int, int_to_bytes, motor_steps_to_angle


__all__ = ["GetID", "GetStatus", "GetCurrent", "GetActualPosition"]


class GetID(Command):
    """Commands the positioners to reply with their positioner id."""

    command_id = CommandID.GET_ID
    broadcastable = True
    timeout = 1
    safe = True
    bootloader = True

    def get_ids(self) -> List:
        """Returns a list of positioners that replied back."""

        return [reply.positioner_id for reply in self.replies]


class GetStatus(Command):
    """Gets the status bits for the positioner."""

    command_id = CommandID.GET_STATUS
    broadcastable = True
    safe = True
    bootloader = True

    def get_positioner_status(self) -> Dict[int, int]:
        """Returns the status flag for each positioner that replied."""

        return {reply.positioner_id: bytes_to_int(reply.data) for reply in self.replies}


class GetActualPosition(Command):
    """Gets the current position of the alpha and beta arms."""

    command_id = CommandID.GET_ACTUAL_POSITION
    broadcastable = False
    safe = True

    def get_positions(self) -> Dict[int, numpy.ndarray]:
        """Returns the positions of alpha and beta in degrees.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        positions = {}
        for reply in self.replies:
            pid = reply.positioner_id
            data = reply.data

            beta = bytes_to_int(data[4:], dtype="i4")
            alpha = bytes_to_int(data[0:4], dtype="i4")

            positions[pid] = numpy.array(motor_steps_to_angle(alpha, beta))

        return positions

    @staticmethod
    def encode(alpha, beta):
        """Returns the position as a bytearray in positioner units."""

        alpha_motor, beta_motor = motor_steps_to_angle(alpha, beta, inverse=True)

        alpha_bytes = int_to_bytes(int(alpha_motor), "i4")
        beta_bytes = int_to_bytes(int(beta_motor), "i4")

        data = alpha_bytes + beta_bytes

        return data


class GetCurrent(Command):
    """Gets the current of the alpha and beta motors."""

    command_id = CommandID.GET_CURRENT
    broadcastable = False
    safe = True

    def get_currents(self) -> Dict[int, numpy.ndarray]:
        """Returns a dictionary of current of alpha and beta for each positioner.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.
        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        currents = {}

        for reply in self.replies:
            data = reply.data

            beta = bytes_to_int(data[4:], dtype="i4")
            alpha = bytes_to_int(data[0:4], dtype="i4")

            currents[reply.positioner_id] = numpy.array([alpha, beta])

        return currents


class GetTemperature(Command):
    """Gets the temperature from the board temperature sensor, in C."""

    command_id = CommandID.GET_RAW_TEMPERATURE
    broadcastable = False
    safe = True

    def get_temperatures(self) -> Dict[int, int]:
        """Returns the temperature in Celsius.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.
        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        temperatures = {}

        for reply in self.replies:
            data = self.replies[0].data
            rawT = bytes_to_int(data, dtype="u4")  # Raw temperature
            temperatures[reply.positioner_id] = rawT

        return temperatures


class SwitchLEDOn(Command):
    """Switched the positioner LED on."""

    command_id = CommandID.SWITCH_LED_ON
    broadcastable = False
    safe = True


class SwitchLEDOff(Command):
    """Switched the positioner LED on."""

    command_id = CommandID.SWITCH_LED_ON
    broadcastable = False
    safe = True
