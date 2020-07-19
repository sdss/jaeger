#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2020-07-15
# @Filename: calibration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

from jaeger import log
from jaeger.commands import Command, CommandID
from jaeger.exceptions import JaegerError
from jaeger.maskbits import PositionerStatus


__ALL__ = ['calibration_positioner', 'StartDatumCalibration',
           'StartMotorCalibration', 'StartCoggingCalibration',
           'SaveInternalCalibration']


async def calibrate_positioner(fps, positioner_id):
    """Runs the calibration process and saves it to the internal memory.

    Parameters
    ----------
    fps : .FPS
        The instance of `.FPS` that will receive the trajectory.
    positioner_id : int
        The ID of the positioner to calibrate.

    Raises
    ------
    JaegerError
        If encounters a problem during the process.

    Examples
    --------
    ::

        >>> fps = FPS()
        >>> await fps.initialise()

        # Calibrate positioner 31.
        >>> await calibrate_positioner(fps, 31)

    """

    log.info(f'Calibrating positioner {positioner_id}.')

    if positioner_id not in fps.positioners:
        raise JaegerError(f'Positioner {positioner_id} not found.')

    positioner = fps[positioner_id]

    if fps.pollers.running:
        log.debug('Stopping pollers')
        await fps.pollers.stop()

    log.info('Starting motor calibration.')

    cmd = await fps.send_command(CommandID.START_MOTOR_CALIBRATION,
                                 positioner_id=positioner_id)

    if cmd.status.failed:
        raise JaegerError('Motor calibration failed.')

    await asyncio.sleep(1)
    await positioner.wait_for_status([PositionerStatus.DISPLACEMENT_COMPLETED,
                                      PositionerStatus.MOTOR_ALPHA_CALIBRATED,
                                      PositionerStatus.MOTOR_BETA_CALIBRATED])

    log.info('Starting datum calibration.')
    cmd = await fps.send_command(CommandID.START_DATUM_CALIBRATION,
                                 positioner_id=positioner_id)
    if cmd.status.failed:
        raise JaegerError('Datum calibration failed.')

    await asyncio.sleep(1)
    await positioner.wait_for_status([PositionerStatus.DISPLACEMENT_COMPLETED,
                                      PositionerStatus.DATUM_ALPHA_CALIBRATED,
                                      PositionerStatus.DATUM_BETA_CALIBRATED])

    log.info('Starting cogging calibration.')
    cmd = await fps.send_command(CommandID.START_COGGING_CALIBRATION,
                                 positioner_id=positioner_id)
    if cmd.status.failed:
        raise JaegerError('Cogging calibration failed.')

    await asyncio.sleep(1)
    result = await positioner.wait_for_status([PositionerStatus.COGGING_ALPHA_CALIBRATED,
                                               PositionerStatus.COGGING_BETA_CALIBRATED],
                                              timeout=1800)
    if result is False:
        raise JaegerError('Cogging calibration timed out')

    log.info('Saving calibration.')
    cmd = await fps.send_command(CommandID.SAVE_INTERNAL_CALIBRATION,
                                 positioner_id=positioner_id)
    if cmd.status.failed:
        raise JaegerError('Saving calibration failed.')

    log.info(f'Positioner {positioner_id} has been calibrated.')

    return


class StartDatumCalibration(Command):
    """Indicates that the transmission for the trajectory has ended."""

    command_id = CommandID.START_DATUM_CALIBRATION
    broadcastable = False
    move_command = True


class StartMotorCalibration(Command):
    """Aborts sending a trajectory."""

    command_id = CommandID.START_MOTOR_CALIBRATION
    broadcastable = False
    move_command = True


class StartCoggingCalibration(Command):
    """Starts the trajectories."""

    command_id = CommandID.START_COGGING_CALIBRATION
    broadcastable = False
    move_command = True


class SaveInternalCalibration(Command):
    """Stop the trajectories."""

    command_id = CommandID.SAVE_INTERNAL_CALIBRATION
    broadcastable = False
    move_command = False
