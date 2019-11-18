#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-08
# @Filename: trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import pathlib
import warnings

import numpy
from ruamel.yaml import YAML

from jaeger import config, log, maskbits
from jaeger.commands import MOTOR_STEPS, TIME_STEP, Command, CommandID
from jaeger.core.exceptions import FPSLockedError, JaegerUserWarning
from jaeger.utils import int_to_bytes


__ALL__ = ['send_trajectory', 'SendNewTrajectory', 'SendTrajectoryData',
           'TrajectoryDataEnd', 'TrajectoryTransmissionAbort',
           'StartTrajectory', 'StopTrajectory']


async def send_trajectory(fps, trajectories, kaiju_check=True):
    """Sends a set of trajectories to the positioners.

    .. danger:: This method can cause the positioners to collide if it is
        commanded with ``kaiju_check=False``.

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
    kaiju_check : `bool`
        Whether to check the trajectories with kaiju before sending it.

    Examples
    --------
    ::

        >>> fps = FPS()
        >>> await fps.initialise()

        # Send a trajectory with two points for positioner 4
        >>> await fps.send_trajectory({1: {'alpha': [(90, 0), (91, 3)],
                                           'beta': [(20, 0), (23, 4)]}})

    """

    if fps.locked:
        raise FPSLockedError('FPS is locked. Cannot send trajectories.')

    PosStatus = maskbits.PositionerStatus

    if isinstance(trajectories, (str, pathlib.Path)):
        yaml = YAML(typ='safe')
        trajectories = yaml.load(open(trajectories))
    elif isinstance(trajectories, dict):
        pass
    else:
        raise ValueError('invalid trajectory data.')

    if kaiju_check:
        # TODO: implement call to kaiju
        pass
    else:
        warnings.warn('about to send a trajectory that has not been checked '
                      'by kaiju. This will end up in tears.', JaegerUserWarning)

    log.info('stopping the pollers before sending the trajectory.')
    await fps.pollers.stop()

    await asyncio.sleep(1)

    try:
        await fps.update_status(positioners=list(trajectories.keys()), timeout=1.)
    except KeyError as ee:
        log.error(f'some positioners in the trajectory are not connected: {ee}')
        return False

    n_points = {}
    max_time = 0.0

    # Check that all positioners are ready to receive a new trajectory.
    for pos_id in trajectories:

        positioner = fps.positioners[pos_id]
        status = positioner.status

        if (PosStatus.DATUM_INITIALIZED not in status or
                PosStatus.DISPLACEMENT_COMPLETED not in status):
            log.error(f'positioner_id={pos_id} is not ready to receive a trajectory.')
            return False

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
                    log.error('at least one SEND_TRAJECTORY_COMMAND failed. Aborting.')
                    return False

    # Finalise the trajectories
    end_traj_cmds = await fps.send_to_all('TRAJECTORY_DATA_END',
                                          positioners=list(trajectories.keys()))

    failed = False
    for cmd in end_traj_cmds:

        if cmd.status.failed:
            await fps.abort_trajectory(trajectories.keys())
            failed = True
            break

        if maskbits.ResponseCode.INVALID_TRAJECTORY in cmd.replies[0].response_code:
            log.error(f'positioner_id={cmd.positioner_id} got an '
                      f'INVALID_TRAJECTORY reply. Aborting trajectory.')
            await fps.abort_trajectory(trajectories.keys())
            failed = True
            break

    if failed:
        log.info('restarting the pollers.')
        fps.pollers.start()
        return False

    # Prepare to start the trajectories. Make position polling faster and
    # output expected time.
    log.info(f'expected time to complete trajectory: {max_time:.2f} seconds.')

    log.info('restarting the pollers.')
    fps.pollers.start()

    await fps.pollers.position.set_delay(0.5)

    # Start trajectories
    await fps.send_command('START_TRAJECTORY', positioner_id=0, timeout=1,
                           n_positioners=len(trajectories))

    # Wait approximate time before starting to poll for status
    await asyncio.sleep(0.95 * max_time, loop=fps.loop)

    remaining_time = max_time - 0.95 * max_time

    # Wait until all positioners have completed.
    wait_status = [fps.positioners[pos_id].wait_for_status(
        PosStatus.DISPLACEMENT_COMPLETED,
        timeout=remaining_time + 3,
        delay=0.1)
        for pos_id in trajectories]
    results = await asyncio.gather(*wait_status, loop=fps.loop)

    if not all(results):
        log.error('some positioners did not complete the move.')
        return False

    log.info('all positioners have reached their final positions.')

    # Restore default polling time
    await fps.pollers.set_delay()

    return True


class SendNewTrajectory(Command):
    """Starts a new trajectory and sends the number of points."""

    command_id = CommandID.SEND_NEW_TRAJECTORY
    broadcastable = False

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
