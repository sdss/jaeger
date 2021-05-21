#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-08
# @Filename: trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import pathlib
import time

from typing import TYPE_CHECKING, Dict, List, Tuple, cast

import numpy

import drift
from sdsstools import read_yaml_file

from jaeger import config, log, maskbits
from jaeger.commands import Command, CommandID
from jaeger.exceptions import FPSLockedError, TrajectoryError
from jaeger.utils import int_to_bytes


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = [
    "send_trajectory",
    "SendNewTrajectory",
    "SendTrajectoryData",
    "TrajectoryDataEnd",
    "TrajectoryTransmissionAbort",
    "StartTrajectory",
    "StopTrajectory",
    "Trajectory",
]


MOTOR_STEPS = config["positioner"]["motor_steps"]
TIME_STEP = config["positioner"]["time_step"]


TrajectoryDataType = Dict[int, Dict[str, List[Tuple[float, float]]]]


async def send_trajectory(
    fps: FPS,
    trajectories: str | pathlib.Path | TrajectoryDataType,
    use_sync_line=True,
):
    """Sends a set of trajectories to the positioners.

    This is a low-level function that raises errors when a problem is
    encountered. Most users should use `.FPS.send_trajectory` instead.
    `.send_trajectory` automates `.Trajectory` by calling the different
    methods in order, but provides less control.

    Parameters
    ----------
    fps
        The instance of `.FPS` that will receive the trajectory.
    trajectories
        Either a path to a YAML file to read or a dictionary with the
        trajectories. In either case the format must be a dictionary in
        which the keys are the ``positioner_ids`` and each value is a
        dictionary containing two keys: ``alpha`` and ``beta``, each
        pointing to a list of tuples ``(position, time)``, where
        ``position`` is in degrees and ``time`` is in seconds.
    use_sync_line
        If `True`, the SYNC line will be used to synchronise the beginning of
        all trajectories. Otherwise a `.START_TRAJECTORY` command will be sent
        over the CAN network.

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
        >>> await fps.send_trajectory({4: {'alpha': [(90, 0), (91, 3)],
                                           'beta': [(20, 0), (23, 4)]}})

    """

    traj = Trajectory(fps, trajectories)

    if use_sync_line:
        if not fps.ieb or fps.ieb.disabled:
            raise TrajectoryError("IEB is not connected. Cannot use SYNC line.")
        if (await fps.ieb.get_device("sync").read())[0] == "closed":
            raise TrajectoryError("The SYNC line is on high.")

    log.debug("sending trajectory data.")
    await traj.send()

    if traj.failed:
        raise TrajectoryError("something went wrong sending the trajectory.")

    log.info(f"trajectory successfully sent in {traj.data_send_time:1f} seconds.")
    log.info(f"expected time to complete trajectory: {traj.move_time:.2f} seconds.")

    log.info("starting trajectory ...")
    result = await traj.start(use_sync_line=use_sync_line)
    if traj.failed or not result:
        raise TrajectoryError("something went wrong starting the trajectory.")

    log.info("all positioners have successfully reached their positions.")

    return True


class Trajectory(object):
    """Prepares and sends a trajectory to the FPS.

    Most user will prefer using `.FPS.send_trajectory`, which automates the
    process. This class provides fine-grain control over the process.

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
    Given the following two-point trajectory for positioner 4 ::

        points = {4: {'alpha': [(90, 0), (91, 3)],
                    'beta': [(20, 0), (23, 4)]}}

    the normal process to execute the trajectory is ::

        trajectory = Trajectory(fps, points)
        await trajectory.send()    # Sends the trajectory but does not yet execute it.
        await trajectory.start()   # This starts the trajectory (positioners move).

    """

    def __init__(
        self,
        fps: FPS,
        trajectories: str | pathlib.Path | TrajectoryDataType,
    ):

        self.fps = fps
        self.trajectories: TrajectoryDataType

        if self.fps.locked:
            raise FPSLockedError("FPS is locked. Cannot send trajectories.")

        if self.fps.moving:
            raise TrajectoryError("the FPS is moving. Cannot send new trajectory.")

        if isinstance(trajectories, (str, pathlib.Path)):
            self.trajectories = cast(TrajectoryDataType, read_yaml_file(trajectories))
        elif isinstance(trajectories, dict):
            self.trajectories = trajectories
        else:
            raise TrajectoryError("invalid trajectory data.")

        self.validate()

        #: Number of points sent to each positioner as a tuple ``(alpha, beta)``.
        self.n_points = {}

        #: The time required to complete the trajectory.
        self.move_time = None

        #: How long it took to send the trajectory.
        self.data_send_time = None

        self.failed = False

        self._ready_to_start = False

    def validate(self):
        """Validates the trajectory."""

        if len(self.trajectories) == 0:
            raise TrajectoryError("trajectory is empty.")

        if len(self.trajectories) != len(numpy.unique(list(self.trajectories.keys()))):
            raise TrajectoryError("duplicate positioner trajectories.")

        for pid in self.trajectories:
            trajectory = self.trajectories[pid]

            if "alpha" not in trajectory or "beta" not in trajectory:
                raise TrajectoryError(f"positioner {pid} missing alpha or beta data.")

            for arm in ["alpha", "beta"]:
                data = numpy.array(list(zip(*trajectory[arm]))[0])

                if numpy.any(data > 360) or numpy.any(data < 0):
                    raise TrajectoryError(f"positioner {pid} has points out of range.")

                if arm == "beta":
                    if config.get("safe_mode", False):
                        if config["safe_mode"] is True:
                            min_beta = 160
                        else:
                            min_beta = config["safe_mode"]["min_beta"]
                        if numpy.any(data < min_beta):
                            raise TrajectoryError(
                                f"positioner {pid}: safe mode is "
                                f"on and beta < {min_beta}."
                            )

    async def send(self):
        """Sends the trajectory but does not start it."""

        self.move_time = 0.0

        await self.abort_trajectory()

        if not await self.fps.update_status(
            positioner_ids=list(self.trajectories.keys()),
            timeout=1.0,
        ):
            self.failed = True
            raise TrajectoryError("some positioners did not respond.")

        # Check that all positioners are ready to receive a new trajectory.
        for pos_id in self.trajectories:

            positioner = self.fps.positioners[pos_id]
            status = positioner.status

            if positioner.disabled:
                self.failed = True
                raise TrajectoryError(
                    f"positioner_id={pos_id} is disabled but "
                    "included in the trajectory."
                )

            if (
                positioner.flags.DATUM_ALPHA_INITIALIZED not in status
                or positioner.flags.DATUM_BETA_INITIALIZED not in status
                or positioner.flags.DISPLACEMENT_COMPLETED not in status
            ):
                self.failed = True
                raise TrajectoryError(
                    f"positioner_id={pos_id} is not ready to receive a trajectory."
                )

            traj_pos = self.trajectories[pos_id]

            self.n_points[pos_id] = (len(traj_pos["alpha"]), len(traj_pos["beta"]))

            # Gets maximum time for this positioner
            max_time_pos = max(
                [
                    max(list(zip(*traj_pos["alpha"]))[1]),
                    max(list(zip(*traj_pos["beta"]))[1]),
                ]
            )

            # Updates the global trajectory max time.
            if max_time_pos > self.move_time:
                self.move_time = max_time_pos

        # Starts trajectory
        new_traj_cmds = [
            self.fps.send_command(
                "SEND_NEW_TRAJECTORY",
                positioner_id=pos_id,
                n_alpha=self.n_points[pos_id][0],
                n_beta=self.n_points[pos_id][1],
            )
            for pos_id in self.trajectories
        ]

        await asyncio.gather(*new_traj_cmds)

        start_trajectory_send_time = time.time()

        # How many points from the trajectory are we putting in each command.
        n_chunk = config["positioner"]["trajectory_data_n_points"]

        # Gets the maximum number of points for each arm for all positioners.
        max_points = numpy.max(list(self.n_points.values()), axis=0)
        max_points = {"alpha": max_points[0], "beta": max_points[1]}

        # Send chunks of size n_chunk to all the positioners in parallel.
        # Do alpha first, then beta.
        for arm in ["alpha", "beta"]:

            for jj in range(0, max_points[arm], n_chunk):

                data_cmds = []

                for pos_id in self.trajectories:

                    arm_chunk = self.trajectories[pos_id][arm][jj : jj + n_chunk]
                    if len(arm_chunk) == 0:
                        continue

                    data_cmds.append(
                        self.fps.send_command(
                            "SEND_TRAJECTORY_DATA",
                            positioner_id=pos_id,
                            positions=arm_chunk,
                        )
                    )

                await asyncio.gather(*data_cmds)

                for cmd in data_cmds:
                    if cmd.status.failed or cmd.status.timed_out:
                        self.failed = True
                        raise TrajectoryError(
                            "at least one SEND_TRAJECTORY_COMMAND failed."
                        )

        # Finalise the trajectories
        end_traj_cmds = await self.fps.send_to_all(
            "TRAJECTORY_DATA_END", positioners=list(self.trajectories.keys())
        )

        for cmd in end_traj_cmds:

            if cmd.status.failed:
                self.failed = True
                raise TrajectoryError("TRAJECTORY_DATA_END failed.")

            if maskbits.ResponseCode.INVALID_TRAJECTORY in cmd.replies[0].response_code:
                self.failed = True
                raise TrajectoryError(
                    f"positioner_id={cmd.positioner_id} got an "
                    f"INVALID_TRAJECTORY reply."
                )

        self.data_send_time = time.time() - start_trajectory_send_time

        self._ready_to_start = True
        self.failed = False

        return True

    async def start(self, use_sync_line=True):
        """Starts the trajectory."""

        if not self._ready_to_start or self.failed:
            raise TrajectoryError("the trajectory has not been sent.")

        if use_sync_line:
            if not self.fps.ieb or self.fps.ieb.disabled:
                raise TrajectoryError("IEB is not connected. Cannot use SYNC line.")
            if (await self.fps.ieb.get_device("sync").read())[0] == "closed":
                raise TrajectoryError("The SYNC line is on high.")

        for positioner_id in list(self.trajectories.keys()):
            self.fps[positioner_id].move_time = self.move_time

        if use_sync_line:

            sync = self.fps.ieb.get_device("sync")
            assert isinstance(sync, drift.Relay)

            # Set SYNC line to high.
            await sync.close()

            await asyncio.sleep(0.5)

            # Reset SYNC line.
            await sync.open()

        else:

            # Start trajectories
            command = await self.fps.send_command(
                "START_TRAJECTORY",
                positioner_id=0,
                timeout=1,
                n_positioners=len(self.trajectories),
            )

            if command.status.failed:
                await self.fps.stop_trajectory()
                self.failed = True
                raise TrajectoryError("START_TRAJECTORY failed")

        try:
            await self.fps.pollers.set_delay(1)
            use_pollers = True
        except Exception as ee:
            use_pollers = False
            log.error(f"failed setting poller delay: {ee}.")

        assert self.move_time is not None, "move_time not set."

        # Wait approximate time before starting to poll for status
        await asyncio.sleep(0.95 * self.move_time)

        remaining_time = self.move_time - 0.95 * self.move_time

        # Wait until all positioners have completed.
        wait_status = [
            self.fps.positioners[pos_id].wait_for_status(
                self.fps.positioners[pos_id].flags.DISPLACEMENT_COMPLETED,
                timeout=remaining_time + 3,
                delay=0.1,
            )
            for pos_id in self.trajectories
        ]
        results = await asyncio.gather(*wait_status)

        if not all(results):
            if use_pollers:
                await self.fps.pollers.set_delay()
            self.failed = True
            raise TrajectoryError("some positioners did not complete the move.")

        # Restore default polling time
        if use_pollers:
            await self.fps.pollers.set_delay()

        return True

    async def abort_trajectory(self):
        """Aborts the trajectory transmission."""

        if not await self.fps.send_to_all(
            "TRAJECTORY_TRANSMISSION_ABORT", positioners=list(self.trajectories.keys())
        ):
            raise TrajectoryError("cannot abort trajectory transmission.")


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
        kwargs["data"] = data

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

        positions[:, 0] = positions[:, 0] / 360.0 * MOTOR_STEPS
        positions[:, 1] /= TIME_STEP

        self.trajectory_points = positions.astype(numpy.int32)

        data = []
        for angle, tt in self.trajectory_points:
            data.append(int_to_bytes(angle, dtype="i4") + int_to_bytes(tt, dtype="i4"))

        kwargs["data"] = data

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
    move_command = False
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
