#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-03
# @Filename: goto.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy

import jaeger
from jaeger import config
from jaeger.commands import Command, CommandID
from jaeger.exceptions import JaegerError
from jaeger.utils import (
    bytes_to_int,
    get_goto_move_time,
    int_to_bytes,
    motor_steps_to_angle,
)

from .trajectory import send_trajectory


if TYPE_CHECKING:
    from jaeger.fps import FPS


__all__ = [
    "GoToDatums",
    "GoToDatumAlpha",
    "GoToDatumBeta",
    "GotoAbsolutePosition",
    "GotoRelativePosition",
    "SetActualPosition",
    "SetSpeed",
    "SetCurrent",
    "goto",
]


TIME_STEP = jaeger.config["positioner"]["time_step"]


class GoToDatums(Command):
    """Initialises and zeroes the positioner."""

    command_id = CommandID.GO_TO_DATUMS
    broadcastable = False
    move_command = True


class GoToDatumAlpha(Command):
    """Initialises and zeroes the alpha arm of the positioner."""

    command_id = CommandID.GO_TO_DATUM_ALPHA
    broadcastable = False
    move_command = True


class GoToDatumBeta(Command):
    """Initialises and zeroes the beta arm of the positioner."""

    command_id = CommandID.GO_TO_DATUM_BETA
    broadcastable = False
    move_command = True


class GotoAbsolutePosition(Command):
    """Moves alpha and beta to absolute positions in degrees."""

    command_id = CommandID.GO_TO_ABSOLUTE_POSITION
    broadcastable = False
    move_command = True

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):

        if alpha is not None and beta is not None:
            alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

            alpha_bytes = int_to_bytes(alpha_steps, dtype="i4")
            beta_bytes = int_to_bytes(beta_steps, dtype="i4")

            data = alpha_bytes + beta_bytes
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def decode(data):
        """Decodes message data into alpha and beta moves."""

        alpha_steps = bytes_to_int(data[0:4], dtype="i4")
        beta_steps = bytes_to_int(data[4:8], dtype="i4")

        return motor_steps_to_angle(alpha_steps, beta_steps)

    def get_replies(self) -> Dict[int, Tuple[float, float]]:
        return self.get_move_time()

    def get_move_time(self):
        """Returns the time needed to move to the commanded position.

        Raises
        ------
        ValueError
            If no reply has been received or the data cannot be parsed.

        """

        move_times = {}
        for reply in self.replies:
            data = reply.data

            alpha = bytes_to_int(data[0:4], dtype="i4")
            beta = bytes_to_int(data[4:], dtype="i4")

            move_times[reply.positioner_id] = [alpha * TIME_STEP, beta * TIME_STEP]

        return move_times


class GotoRelativePosition(GotoAbsolutePosition):
    """Moves alpha and beta a relative number of degrees."""

    command_id = CommandID.GO_TO_RELATIVE_POSITION
    broadcastable = False
    move_command = True


class SetActualPosition(Command):
    """Sets the current position of the alpha and beta arms."""

    command_id = CommandID.SET_ACTUAL_POSITION
    broadcastable = False
    safe = True
    move_command = True  # Technically not a move command but we don't
    # want to issue it during a move.

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):

        if alpha is not None and beta is not None:

            alpha_steps, beta_steps = motor_steps_to_angle(alpha, beta, inverse=True)

            alpha_bytes = int_to_bytes(int(alpha_steps), dtype="i4")
            beta_bytes = int_to_bytes(int(beta_steps), dtype="i4")

            data = alpha_bytes + beta_bytes

            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


class SetSpeed(Command):
    """Sets the speeds of the alpha and beta motors."""

    command_id = CommandID.SET_SPEED
    broadcastable = False
    safe = True
    move_command = False

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):

        if alpha is not None and beta is not None:
            assert alpha >= 0 and beta >= 0, "invalid speed."

            data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)

    @staticmethod
    def encode(alpha, beta):
        """Encodes the alpha and beta speed as bytes."""

        data_speed = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))

        return data_speed


class SetCurrent(Command):
    """Sets the current of the alpha and beta motors."""

    command_id = CommandID.SET_CURRENT
    broadcastable = False
    safe = True
    move_command = True

    def __init__(
        self,
        positioner_ids: int | List[int],
        alpha: float | None = None,
        beta: float | None = None,
        **kwargs,
    ):

        if alpha is not None and beta is not None:
            assert alpha >= 0 and beta >= 0, "invalid current."

            data = int_to_bytes(int(alpha)) + int_to_bytes(int(beta))
            kwargs["data"] = data

        super().__init__(positioner_ids, **kwargs)


async def goto(
    fps: FPS,
    positioner_ids: List[int],
    alpha: float | list | numpy.ndarray,
    beta: float | list | numpy.ndarray,
    speed: Optional[Tuple[float, float]] = None,
    relative: bool = False,
    use_sync_line: bool = None,
):
    """Send positioners to a given position using a trajectory.

    Parameters
    ----------
    fps
        The `.FPS` instance.
    positioner_ids
        The list of positioner_ids to command.
    alpha
        The alpha angle. Can be an array with the same size of the list of positioner
        IDs. Otherwise sends all the positioners to the same angle.
    beta
        The beta angle.
    speed
        As a tuple, the alpha and beta speeds to use. If `None`, uses the default ones.
    relative
        If `True`, ``alpha`` and ``beta`` are considered relative angles.
    use_sync_line
        Whether to use the SYNC line to start the trajectories.

    """

    if not isinstance(alpha, (list, tuple, numpy.ndarray)):
        alpha = numpy.tile(alpha, len(positioner_ids))
    if not isinstance(beta, (list, tuple, numpy.ndarray)):
        beta = numpy.tile(beta, len(positioner_ids))

    alpha = numpy.array(alpha)
    beta = numpy.array(beta)

    assert len(alpha) == len(positioner_ids) and len(beta) == len(positioner_ids)

    if alpha is None or beta is None:
        raise JaegerError("alpha and beta must be non-null.")

    if speed is None:
        default_speed = config["positioner"]["motor_speed"]
        speed = (default_speed, default_speed)
    else:
        if len(speed) != 2 or speed[0] is None or speed[1] is None:
            raise JaegerError("Invalid speed.")

    speed_array = numpy.array(speed)
    if numpy.any(speed_array <= 0) or numpy.any(speed_array > 5000):
        raise JaegerError("Speed out of bounds.")

    await fps.update_position(positioner_ids=positioner_ids)

    trajectories = {}
    for i, pid in enumerate(positioner_ids):
        pos = fps[pid]

        if pos.alpha is None or pos.beta is None:
            raise JaegerError(f"Positioner {pid}: cannot goto with unknown position.")

        current_alpha = pos.alpha
        current_beta = pos.beta

        if relative is True:
            alpha_end = current_alpha + alpha[i]
            beta_end = current_beta + beta[i]
        else:
            alpha_end = alpha[i]
            beta_end = beta[i]

        alpha_delta = abs(alpha_end - current_alpha)
        beta_delta = abs(beta_end - current_beta)

        time_end = [
            get_goto_move_time(alpha_delta, speed=speed[0]),
            get_goto_move_time(beta_delta, speed=speed[1]),
        ]

        trajectories[pid] = {
            "alpha": [(current_alpha, 0.1), (alpha_end, time_end[0] + 0.1)],
            "beta": [(current_beta, 0.1), (beta_end, time_end[1] + 0.1)],
        }

    return await send_trajectory(fps, trajectories, use_sync_line=use_sync_line)
