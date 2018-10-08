#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-03
# @Filename: goto.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-08 01:24:16

import numpy

from jaeger.commands import MOTOR_STEPS, TIME_STEP, Command, CommandID
from jaeger.utils import bytes_to_int, int_to_bytes


__ALL__ = ['InitialiseDatums', 'StartTrajectory', 'GotoAbsolutePosition',
           'SetSpeed']


class InitialiseDatums(Command):
    """Initialises and zeroes the positioner."""

    command_id = CommandID.INITIALIZE_DATUMS
    broadcastable = False


class StartTrajectory(Command):

    command_id = CommandID.START_TRAJECTORY
    broadcastable = False


class GotoAbsolutePosition(Command):

    command_id = CommandID.GO_TO_ABSOLUTE_POSITION
    broadcastable = False

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        alpha_steps = int(alpha / 360. * MOTOR_STEPS)
        beta_steps = int(beta / 360. * MOTOR_STEPS)

        data = int_to_bytes(alpha_steps) + int_to_bytes(beta_steps)
        kwargs['data'] = data

        super().__init__(**kwargs)

    def get_move_time(self):
        """Returns the time needed to move to the commanded position.

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

        return numpy.array([alpha, beta]) * TIME_STEP


class GotoRelativePosition(GotoAbsolutePosition):

    command_id = CommandID.GO_TO_RELATIVE_POSITION
    broadcastable = False


class SetSpeed(Command):

    command_id = CommandID.SET_SPEED
    broadcastable = False

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
        kwargs['data'] = data

        super().__init__(**kwargs)
