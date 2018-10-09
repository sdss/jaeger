#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-08
# @Filename: trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-08 18:30:00


from jaeger.commands import MOTOR_STEPS, TIME_STEP, Command, CommandID
from jaeger.utils import int_to_bytes


__ALL__ = ['SendNewTrajectory', 'SendTrajectoryData', 'TrajectoryDataEnd',
           'TrajectoryTransmissionAbort', 'StartTrajectory', 'StopTrajectory']


class SendNewTrajectory(Command):
    """Starts a new trajectory and sends the number of points."""

    command_id = CommandID.SEND_NEW_TRAJECTORY
    broadcastable = False

    def __init__(self, n_alpha, n_beta, **kwargs):

        alpha_positions = int(n_alpha)
        beta_positions = int(n_beta)

        assert alpha_positions > 0 and beta_positions > 0

        data = int_to_bytes(beta_positions) + int_to_bytes(alpha_positions)
        kwargs['data'] = data

        super().__init__(**kwargs)


class SendTrajectoryData(Command):
    """Sends a data point in the trajectory."""

    command_id = CommandID.SEND_TRAJECTORY_DATA
    broadcastable = False

    def __init__(self, angle, time, **kwargs):

        angle_steps = int(angle / 360. * MOTOR_STEPS)
        time_steps = int(time / TIME_STEP)

        data = int_to_bytes(time_steps) + int_to_bytes(angle_steps)
        kwargs['data'] = data

        super().__init__(**kwargs)


class TrajectoryDataEnd(Command):
    """Indicates that the transmission for the trajectory has ended."""

    command_id = CommandID.TRAJECTORY_DATA_END
    broadcastable = False


class TrajectoryTransmissionAbort(Command):
    """Aborts sending a trajectory."""

    command_id = CommandID.TRAJECTORY_TRANSMISSION_ABORT
    broadcastable = False


class StartTrajectory(Command):
    """Starts the trajectories."""

    command_id = CommandID.START_TRAJECTORY
    broadcastable = True


class StopTrajectory(Command):
    """Stop the trajectories."""

    command_id = CommandID.STOP_TRAJECTORY
    broadcastable = True
