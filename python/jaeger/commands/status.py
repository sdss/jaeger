#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-07 23:33:55


import numpy

from jaeger.commands import MOTOR_STEPS, Command, CommandID
from jaeger.utils import bytes_to_int


__ALL__ = ['GetID', 'GetStatus']


class GetID(Command):
    """Commands the positioners to reply with their positioner id."""

    command_id = CommandID.GET_ID
    broadcastable = True

    def get_ids(self):
        """Returns a list of positioners that replied back."""

        return [reply.positioner_id for reply in self.replies]


class GetStatus(Command):

    command_id = CommandID.GET_STATUS
    broadcastable = True


class GetActualPosition(Command):

    command_id = CommandID.GET_ACTUAL_POSTION
    broadcastable = False

    def get_postions(self):
        """Returns the positions of alpha and beta in degrees.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        if len(self.replies) == 0:
            raise ValueError('no positioners have replied to this command.')

        data = self.replies[0].data

        alpha = bytes_to_int(data[0:4])
        beta = bytes_to_int(data[4:])

        return numpy.array([alpha, beta]) / MOTOR_STEPS * 360.
