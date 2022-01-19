#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2020-07-15
# @Filename: calibration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import struct

from typing import Dict, List, Tuple

import numpy

from jaeger import config, log
from jaeger.commands import Command, CommandID
from jaeger.exceptions import JaegerError
from jaeger.maskbits import PositionerStatus as PS
from jaeger.utils import bytes_to_int, int_to_bytes, motor_steps_to_angle


__all__ = [
    "calibrate_positioner",
    "StartDatumCalibration",
    "StartMotorCalibration",
    "StartCoggingCalibration",
    "SaveInternalCalibration",
    "HallOn",
    "HallOff",
]


MOTOR_STEPS = config["positioner"]["motor_steps"]


async def calibrate_positioner(
    fps, positioner_id, motors=True, datums=True, cogging=True
):
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

    log.info(f"Calibrating positioner {positioner_id}.")

    if positioner_id not in fps.positioners:
        raise JaegerError(f"Positioner {positioner_id} not found.")

    positioner = fps[positioner_id]

    if fps.pollers.running:
        log.debug("Stopping pollers")
        await fps.pollers.stop()

    if motors:
        log.info("Starting motor calibration.")

        cmd = await fps.send_command(
            CommandID.START_MOTOR_CALIBRATION,
            positioner_ids=positioner_id,
        )

        if cmd.status.failed:
            raise JaegerError("Motor calibration failed.")

        await asyncio.sleep(1)
        await positioner.wait_for_status(
            [
                PS.DISPLACEMENT_COMPLETED,
                PS.MOTOR_ALPHA_CALIBRATED,
                PS.MOTOR_BETA_CALIBRATED,
            ]
        )
    else:
        log.warning("Skipping motor calibration.")

    if datums:
        log.info("Starting datum calibration.")
        cmd = await fps.send_command(
            CommandID.START_DATUM_CALIBRATION,
            positioner_ids=positioner_id,
        )
        if cmd.status.failed:
            raise JaegerError("Datum calibration failed.")

        await asyncio.sleep(1)
        await positioner.wait_for_status(
            [
                PS.DISPLACEMENT_COMPLETED,
                PS.DATUM_ALPHA_CALIBRATED,
                PS.DATUM_BETA_CALIBRATED,
            ]
        )
    else:
        log.warning("Skipping datum calibration.")

    if cogging:
        log.info("Starting cogging calibration.")
        cmd = await fps.send_command(
            CommandID.START_COGGING_CALIBRATION,
            positioner_ids=positioner_id,
        )
        if cmd.status.failed:
            raise JaegerError("Cogging calibration failed.")

        await asyncio.sleep(1)
        await positioner.wait_for_status(
            [PS.COGGING_ALPHA_CALIBRATED, PS.COGGING_BETA_CALIBRATED]
        )
    else:
        log.warning("Skipping cogging calibration.")

    if motors or datums or cogging:
        log.info("Saving calibration.")
        cmd = await fps.send_command(
            CommandID.SAVE_INTERNAL_CALIBRATION,
            positioner_ids=positioner_id,
        )
        if cmd.status.failed:
            raise JaegerError("Saving calibration failed.")

        log.info(f"Positioner {positioner_id} has been calibrated.")

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


class GetOffset(Command):
    """Gets the motor offsets."""

    command_id = CommandID.GET_OFFSETS
    broadcastable = False
    safe = True

    def get_replies(self) -> Dict[int, numpy.ndarray]:
        return self.get_offsets()

    def get_offsets(self) -> Dict[int, numpy.ndarray]:
        """Returns the alpha and beta offsets, in degrees.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        offsets = {}
        for reply in self.replies:
            pid = reply.positioner_id
            data = reply.data

            alpha = bytes_to_int(data[0:4], dtype="i4")
            beta = bytes_to_int(data[4:], dtype="i4")

            offsets[pid] = numpy.array(motor_steps_to_angle(alpha, beta))

        return offsets


class SetOffsets(Command):
    """Sets the motor offsets."""

    command_id = CommandID.SET_OFFSETS
    broadcastable = False
    safe = True
    move_command = False

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha=None,
        beta=None,
        **kwargs,
    ):

        if alpha is not None and beta is not None:
            alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

            data = int_to_bytes(int(alpha_steps)) + int_to_bytes(int(beta_steps))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


class HallOn(Command):
    """Turns hall sensors ON."""

    command_id = CommandID.HALL_ON
    broadcastable = False
    move_command = False
    safe = True


class HallOff(Command):
    """Turns hall sensors ON."""

    command_id = CommandID.HALL_OFF
    broadcastable = False
    move_command = False
    safe = True


class SetHoldingCurrents(Command):
    """Sets the motors holding currents."""

    command_id = CommandID.SET_HOLDING_CURRENT
    broadcastable = False
    safe = True
    move_command = False

    def __init__(self, positioner_ids, alpha=None, beta=None, **kwargs):

        if alpha is not None and beta is not None:
            data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


class GetHoldingCurrents(Command):
    """Gets the motor offsets."""

    command_id = CommandID.GET_HOLDING_CURRENT
    broadcastable = False
    safe = True

    def get_replies(self) -> Dict[int, numpy.ndarray]:
        return self.get_holding_currents()

    def get_holding_currents(self) -> Dict[int, numpy.ndarray]:
        """Returns the alpha and beta holding currents, in percent.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        currents = {}
        for reply in self.replies:
            data = reply.data

            alpha = bytes_to_int(data[0:4], dtype="i4")
            beta = bytes_to_int(data[4:], dtype="i4")

            currents[reply.positioner_id] = numpy.array([alpha, beta])

        return currents


class PreciseMoveAlphaOn(Command):
    """Turns precise move on alpha ON."""

    command_id = CommandID.SWITCH_ON_PRECISE_MOVE_ALPHA
    broadcastable = False
    move_command = False
    safe = True


class PreciseMoveAlphaOff(Command):
    """Turns precise move on alpha OFF."""

    command_id = CommandID.SWITCH_OFF_PRECISE_MOVE_ALPHA
    broadcastable = False
    move_command = False
    safe = True


class PreciseMoveBetaOn(Command):
    """Turns precise move on beta ON."""

    command_id = CommandID.SWITCH_ON_PRECISE_MOVE_BETA
    broadcastable = False
    move_command = False
    safe = True


class PreciseMoveBetaOff(Command):
    """Turns precise move on beta OFF."""

    command_id = CommandID.SWITCH_OFF_PRECISE_MOVE_BETA
    broadcastable = False
    move_command = False
    safe = True


class SetIncreaseCollisionMargin(Command):
    """Sets the buffer for collision margin."""

    command_id = CommandID.SET_INCREASE_COLLISION_MARGIN
    broadcastable = False
    move_command = False
    safe = False

    def __init__(self, positioner_ids, margin: int, **kwargs):

        data = int_to_bytes(int(margin), dtype="i4")
        kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


class GetAlphaHallCalibration(Command):

    command_id = CommandID.GET_ALPHA_HALL_CALIB
    broadcastable = False
    move_command = False
    safe = True

    def get_replies(self) -> Dict[int, Tuple[int, int, int, int]]:
        return self.get_values()

    def get_values(self) -> dict[int, Tuple[int, int, int, int]]:
        """Returns the ``maxA, maxB, minA, minB`` values."""

        values = {}

        for reply in self.replies:
            values[reply.positioner_id] = struct.unpack("HHHH", reply.data)

        return values


class GetBetaHallCalibration(Command):

    command_id = CommandID.GET_BETA_HALL_CALIB
    broadcastable = False
    move_command = False
    safe = True

    def get_replies(self) -> Dict[int, Tuple[int, int, int, int]]:
        return self.get_values()

    def get_values(self) -> dict[int, Tuple[int, int, int, int]]:
        """Returns the ``maxA, maxB, minA, minB`` values."""

        values = {}

        for reply in self.replies:
            values[reply.positioner_id] = struct.unpack("HHHH", reply.data)

        return values


class GetHallCalibrationError(Command):

    command_id = CommandID.GET_HALL_CALIB_ERROR
    broadcastable = False
    move_command = False
    safe = True

    def get_replies(self) -> Dict[int, Tuple[int, int]]:
        return self.get_values()

    def get_values(self) -> dict[int, Tuple[int, int]]:
        """Returns the alpha and beta error values."""

        values = {}

        for reply in self.replies:
            values[reply.positioner_id] = struct.unpack("ii", reply.data)

        return values
