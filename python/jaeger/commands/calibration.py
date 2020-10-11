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
from jaeger.maskbits import PositionerStatus as PS


__ALL__ = ['calibration_positioner', 'StartDatumCalibration',
           'StartMotorCalibration', 'StartCoggingCalibration',
           'SaveInternalCalibration']


async def calibrate_positioner(fps, positioner_id, motors=True, datums=True,
                               cogging=True):
    """Runs the calibration process and saves it to the internal memory.

    Parameters
    ----------
    fps : .FPS
        The instance of `.FPS` that will receive the trajectory.
    positioner_id : int
        The ID of the positioner to calibrate.
    motors : bool
        Whether to perform the motor calibration.
    datums : bool
        Whether to perform the datums calibration.
    cogging : bool
        Whether to perform the cogging calibration (may take more
        than one hour).

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

    if motors:
        log.info('Starting motor calibration.')

        cmd = await fps.send_command(CommandID.START_MOTOR_CALIBRATION,
                                     positioner_id=positioner_id)

        if cmd.status.failed:
            raise JaegerError('Motor calibration failed.')

        await asyncio.sleep(1)
        await positioner.wait_for_status([PS.DISPLACEMENT_COMPLETED,
                                          PS.MOTOR_ALPHA_CALIBRATED,
                                          PS.MOTOR_BETA_CALIBRATED])
    else:
        log.warning('Skipping motor calibration.')

    if datums:
        log.info('Starting datum calibration.')
        cmd = await fps.send_command(CommandID.START_DATUM_CALIBRATION,
                                     positioner_id=positioner_id)
        if cmd.status.failed:
            raise JaegerError('Datum calibration failed.')

        await asyncio.sleep(1)
        await positioner.wait_for_status([PS.DISPLACEMENT_COMPLETED,
                                          PS.DATUM_ALPHA_CALIBRATED,
                                          PS.DATUM_BETA_CALIBRATED])
    else:
        log.warning('Skipping datum calibration.')

    if cogging:
        log.info('Starting cogging calibration.')
        cmd = await fps.send_command(CommandID.START_COGGING_CALIBRATION,
                                     positioner_id=positioner_id)
        if cmd.status.failed:
            raise JaegerError('Cogging calibration failed.')

        await asyncio.sleep(1)
        await positioner.wait_for_status([PS.COGGING_ALPHA_CALIBRATED,
                                          PS.COGGING_BETA_CALIBRATED])
    else:
        log.warning('Skipping cogging calibration.')

    if motors or datums or cogging:
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
