#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-03
# @Filename: goto.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-03 22:01:59


from jaeger.commands import Command, CommandID
from jaeger.utils import int_to_bytes


__ALL__ = ['InitialiseDatums', 'StartTrajectory', 'GotoAbsolutePosition',
           'SetSpeed']


class InitialiseDatums(Command):
    """Initialises and zeroes the positioner."""

    command_id = CommandID.INITIALIZE_DATUMS
    broadcastable = False
    timeout = 2


class StartTrajectory(Command):

    command_id = CommandID.START_TRAJECTORY
    broadcastable = False
    timeout = 0


class GotoAbsolutePosition(Command):

    command_id = CommandID.GO_TO_ABSOLUTE_POSITION
    broadcastable = False
    timeout = 2

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        alpha_steps = int(alpha / 360. * 2**30)
        beta_steps = int(beta / 360. * 2**30)

        data = int_to_bytes(alpha_steps) + int_to_bytes(beta_steps)
        kwargs['data'] = data

        super().__init__(**kwargs)


class SetSpeed(Command):

    command_id = CommandID.SET_SPEED
    broadcastable = False
    timeout = 2

    def __init__(self, alpha=0.0, beta=0.0, **kwargs):

        data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
        kwargs['data'] = data

        super().__init__(**kwargs)
