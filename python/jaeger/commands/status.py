#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

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

    def get_ids(self):
        """Returns a list of positioners that replied back."""

        return [reply.positioner_id for reply in self.replies]


class GetStatus(Command):
    """Gets the status bits for the positioner."""

    command_id = CommandID.GET_STATUS
    broadcastable = True
    safe = True
    bootloader = True

    def get_positioner_status(self):
        """Returns the positioner status flag for each reply."""

        return [bytes_to_int(reply.data) for reply in self.replies]


class GetActualPosition(Command):
    """Gets the current position of the alpha and beta arms."""

    command_id = CommandID.GET_ACTUAL_POSITION
    broadcastable = False
    safe = True

    def get_positions(self):
        """Returns the positions of alpha and beta in degrees.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        data = self.replies[0].data

        beta = bytes_to_int(data[4:], dtype="i4")
        alpha = bytes_to_int(data[0:4], dtype="i4")

        return numpy.array(motor_steps_to_angle(alpha, beta))

    @staticmethod
    def encode(alpha, beta):
        """Returns the position as a bytearray in positioner units."""

        alpha_motor, beta_motor = motor_steps_to_angle(alpha, beta, inverse=True)

        data = int_to_bytes(int(alpha_motor), "i4") + int_to_bytes(
            int(beta_motor), "i4"
        )

        return data


class GetCurrent(Command):
    """Gets the current of the alpha and beta motors."""

    command_id = CommandID.GET_CURRENT
    broadcastable = False
    safe = True

    def get_current(self):
        """Returns the current of alpha and beta.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.
        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        data = self.replies[0].data

        beta = bytes_to_int(data[4:], dtype="i4")
        alpha = bytes_to_int(data[0:4], dtype="i4")

        return numpy.array([alpha, beta])


class GetTemperature(Command):
    """Gets the temperature from the board temperature sensor, in C."""

    command_id = CommandID.GET_RAW_TEMPERATURE
    broadcastable = False
    safe = True

    def get_temperature(self) -> int:
        """Returns the temperature in Celsius.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.
        """

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        data = self.replies[0].data
        rawT = bytes_to_int(data, dtype="u4")  # Raw temperature

        return rawT
