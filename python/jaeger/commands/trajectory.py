#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-08
# @Filename: trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import pathlib

import numpy
from ruamel.yaml import YAML

from jaeger import config, log, maskbits
from jaeger.commands import MOTOR_STEPS, TIME_STEP, Command, CommandID
from jaeger.core.exceptions import FPSLockedError, TrajectoryError
from jaeger.utils import int_to_bytes


__ALL__ = ['send_trajectory', 'SendNewTrajectory', 'SendTrajectoryData',
           'TrajectoryDataEnd', 'TrajectoryTransmissionAbort',
           'StartTrajectory', 'StopTrajectory']


async def send_trajectory(fps, trajectories):
    """Sends a set of trajectories to the positioners.

    This is a low-level function that raises errors when a problem is
    encountered. Most users should use `.FPS.send_trajectory` instead.

    Parameters
    ----------
    fps : .FPS
        The instance of `.FPS` that will receive the trajectory.
    trajectories : `str` or `dict`
        Either a path to a YAML file to read or a dictionary with the
        trajectories. In either case the format must be a dictionary in
        which the keys are the ``positioner_ids`` and each value is a
        dictionary containing two keys: ``alpha`` and ``beta``, each
        pointing to a list of tuples ``(position, time)``, where
        ``position`` is in degrees and ``time`` is in seconds.

    Raises
    ------
    TrajectoryError
        If encounters a problem sending the trajectory.
    FPSLockedError
        If the FPS is locked.

    Examples
    --------
    ::

        >>> fps = FPS()
        >>> await fps.initialise()

        # Send a trajectory with two points for positioner 4
        >>> await fps.send_trajectory({1: {'alpha': [(90, 0), (91, 3)],
                                           'beta': [(20, 0), (23, 4)]}})

    """

    log.info('starting trajectory ...')

    if fps.locked:
        raise FPSLockedError('FPS is locked. Cannot send trajectories.')

    if fps.moving:
        raise TrajectoryError('the FPS is moving. Cannot send new trajectory.')

    if isinstance(trajectories, (str, pathlib.Path)):
        yaml = YAML(typ='safe')
        trajectories = yaml.load(open(trajectories))
    elif isinstance(trajectories, dict):
        pass
    else:
        raise TrajectoryError('invalid trajectory data.')

    if not await fps.update_status(positioner_id=list(trajectories.keys()), timeout=1.):
        raise TrajectoryError(f'some positioners did not respond.')

    n_points = {}
    max_time = 0.0

    # Check that all positioners are ready to receive a new trajectory.
    for pos_id in trajectories:

        positioner = fps.positioners[pos_id]
        status = positioner.status

        if (positioner.flags.DATUM_ALPHA_INITIALIZED not in status or
                positioner.flags.DATUM_BETA_INITIALIZED not in status or
                positioner.flags.DISPLACEMENT_COMPLETED not in status):
            raise TrajectoryError(f'positioner_id={pos_id} is not '
                                  'ready to receive a trajectory.')

        traj_pos = trajectories[pos_id]

        n_points[pos_id] = (len(traj_pos['alpha']), len(traj_pos['beta']))

        # Gets maximum time for this positioner
        max_time_pos = max([max(list(zip(*traj_pos['alpha']))[1]),
                            max(list(zip(*traj_pos['beta']))[1])])

        # Updates the global trajectory max time.
        if max_time_pos > max_time:
            max_time = max_time_pos

    # Starts trajectory
    new_traj_cmds = [fps.send_command('SEND_NEW_TRAJECTORY',
                                      positioner_id=pos_id,
                                      n_alpha=n_points[pos_id][0],
                                      n_beta=n_points[pos_id][1])
                     for pos_id in trajectories]

    await asyncio.gather(*new_traj_cmds)

    # How many points from the trajectory are we putting in each command.
    n_chunk = config['positioner']['trajectory_data_n_points']

    # Gets the maximum number of points for each arm for all positioners.
    max_points = numpy.max(list(n_points.values()), axis=0)
    max_points = {'alpha': max_points[0], 'beta': max_points[1]}

    # Send chunks of size n_chunk to all the positioners in parallel.
    # Do alpha first, then beta.
    for arm in ['alpha', 'beta']:

        for jj in range(0, max_points[arm], n_chunk):

            data_cmds = []

            for pos_id in trajectories:

                arm_chunk = trajectories[pos_id][arm][jj:jj + n_chunk]
                if len(arm_chunk) == 0:
                    continue

                data_cmds.append(
                    fps.send_command('SEND_TRAJECTORY_DATA',
                                     positioner_id=pos_id,
                                     positions=arm_chunk))

            await asyncio.gather(*data_cmds)

            for cmd in data_cmds:
                if cmd.status.failed:
                    raise TrajectoryError('at least one SEND_TRAJECTORY_COMMAND failed.')

    # Finalise the trajectories
    end_traj_cmds = await fps.send_to_all('TRAJECTORY_DATA_END',
                                          positioners=list(trajectories.keys()))

    for cmd in end_traj_cmds:

        if cmd.status.failed:
            raise TrajectoryError('TRAJECTORY_DATA_END failed.')

        if maskbits.ResponseCode.INVALID_TRAJECTORY in cmd.replies[0].response_code:
            raise TrajectoryError(f'positioner_id={cmd.positioner_id} got an '
                                  f'INVALID_TRAJECTORY reply.')

    # Prepare to start the trajectories. Make position polling faster and
    # output expected time.
    log.info(f'expected time to complete trajectory: {max_time:.2f} seconds.')

    for positioner_id in list(trajectories.keys()):
        fps[positioner_id].move_time = max_time

    # Start trajectories
    command = await fps.send_command('START_TRAJECTORY', positioner_id=0, timeout=1,
                                     n_positioners=len(trajectories))

    if command.status.failed:
        await fps.stop_trajectory()
        raise TrajectoryError('START_TRAJECTORY failed')

    await fps.pollers.set_delay(1)

    # Wait approximate time before starting to poll for status
    await asyncio.sleep(0.95 * max_time, loop=fps.loop)

    remaining_time = max_time - 0.95 * max_time

    # Wait until all positioners have completed.
    wait_status = [fps.positioners[pos_id].wait_for_status(
        fps.positioners[pos_id].flags.DISPLACEMENT_COMPLETED,
        timeout=remaining_time + 3, delay=0.1)
        for pos_id in trajectories]
    results = await asyncio.gather(*wait_status, loop=fps.loop)

    if not all(results):
        await fps.pollers.set_delay()
        raise TrajectoryError('some positioners did not complete the move.')

    log.info('all positioners have reached their final positions.')

    # Restore default polling time
    await fps.pollers.set_delay()

    return True


class SendNewTrajectory(Command):
    """Starts a new trajectory and sends the number of points."""

    command_id = CommandID.SEND_NEW_TRAJECTORY
    broadcastable = False
    move_command = True

    def __init__(self, n_alpha, n_beta, **kwargs):

        alpha_positions = int(n_alpha)
        beta_positions = int(n_beta)

        assert alpha_positions > 0 and beta_positions > 0

        data = int_to_bytes(alpha_positions) + int_to_bytes(beta_positions)
        kwargs['data'] = data

        super().__init__(**kwargs)


class SendTrajectoryData(Command):
    """Sends a data point in the trajectory.

    This command sends multiple messages that represent a full trajectory.

    Parameters
    ----------
    positions : list
        A list of tuples in which the first element is the angle, in degrees,
        and the second is the associated time, in seconds.

    Examples
    --------
        >>> SendTrajectoryData([(90, 10), (88, 12), (80, 20)])

    """

    command_id = CommandID.SEND_TRAJECTORY_DATA
    broadcastable = False
    move_command = True

    def __init__(self, positions, **kwargs):

        positions = numpy.array(positions).astype(numpy.float64)

        positions[:, 0] = positions[:, 0] / 360. * MOTOR_STEPS
        positions[:, 1] /= TIME_STEP

        self.trajectory_points = positions.astype(numpy.int)

        data = []
        for angle, time in self.trajectory_points:
            data.append(int_to_bytes(angle, dtype='i4') + int_to_bytes(time, dtype='i4'))

        kwargs['data'] = data

        super().__init__(**kwargs)


class TrajectoryDataEnd(Command):
    """Indicates that the transmission for the trajectory has ended."""

    command_id = CommandID.TRAJECTORY_DATA_END
    broadcastable = False
    move_command = True


class TrajectoryTransmissionAbort(Command):
    """Aborts sending a trajectory."""

    command_id = CommandID.TRAJECTORY_TRANSMISSION_ABORT
    broadcastable = False
    move_command = True
    safe = True


class StartTrajectory(Command):
    """Starts the trajectories."""

    command_id = CommandID.START_TRAJECTORY
    broadcastable = True
    move_command = True


class StopTrajectory(Command):
    """Stop the trajectories."""

    command_id = CommandID.STOP_TRAJECTORY
    broadcastable = True
    safe = True
