#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-03
# @Filename: goto.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import numpy

from jaeger.commands import TIME_STEP, Command, CommandID
from jaeger.utils import bytes_to_int, int_to_bytes, motor_steps_to_angle


__ALL__ = ['InitialiseDatums', 'StartTrajectory', 'GotoAbsolutePosition',
           'SetSpeed', 'SetCurrent']


class InitialiseDatums(Command):
    """Initialises and zeroes the positioner."""

    command_id = CommandID.INITIALIZE_DATUMS
    broadcastable = False


class GotoAbsolutePosition(Command):
    """Moves alpha and beta to absolute positions in degrees."""

    command_id = CommandID.GO_TO_ABSOLUTE_POSITION
    broadcastable = False

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

        data = int_to_bytes(alpha_steps, dtype='i4') + int_to_bytes(beta_steps, dtype='i4')
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

        beta = bytes_to_int(data[4:], dtype='i4')
        alpha = bytes_to_int(data[0:4], dtype='i4')

        return numpy.array([alpha, beta]) * TIME_STEP


class GotoRelativePosition(GotoAbsolutePosition):
    """Moves alpha and beta a relative number of degrees."""

    command_id = CommandID.GO_TO_RELATIVE_POSITION
    broadcastable = False


class SetActualPosition(Command):
    """Sets the current position of the alpha and beta arms."""

    command_id = CommandID.SET_ACTUAL_POSITION
    broadcastable = False
    safe = True

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

        data = (int_to_bytes(int(alpha_steps), dtype='i4') +
                int_to_bytes(int(beta_steps), dtype='i4'))
        kwargs['data'] = data

        super().__init__(**kwargs)


class SetSpeed(Command):
    """Sets the speeds of the alpha and beta motors."""

    command_id = CommandID.SET_SPEED
    broadcastable = False
    safe = True

    def __init__(self, alpha=0, beta=0, **kwargs):

        assert alpha >= 0 and beta >= 0, 'invalid speed.'

        data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
        kwargs['data'] = data

        super().__init__(**kwargs)


class SetCurrent(Command):
    """Sets the current of the alpha and beta motors."""

    command_id = CommandID.SET_CURRENT
    broadcastable = False

    def __init__(self, alpha=0, beta=0, **kwargs):

        assert alpha >= 0 and beta >= 0, 'invalid current.'

        data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
        kwargs['data'] = data

        super().__init__(**kwargs)
