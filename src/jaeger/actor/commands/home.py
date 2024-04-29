#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-06-05
# @Filename: home.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

import click
import numpy

from jaeger import config
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.kaiju import TrajectoryType, get_path_pair, get_robot_grid

from . import jaeger_parser


if TYPE_CHECKING:
    from jaeger import FPS

    from . import JaegerCommandType


__all__ = ["home"]


SAFE_BETA_MIN: float = 165.0
SAFE_BETA_MAX: float = 181.0

START_ANGLE: float = 5.0
COLLISION_BUFFER: float = 3.2

ALPHA1: float = 118
ALPHA2: float = 58

ALPHA_FOLDED, BETA_FOLDED = config["kaiju"]["lattice_position"]


@jaeger_parser.command()
@click.argument(
    "AXIS",
    type=click.Choice(["alpha", "beta"], case_sensitive=False),
)
@click.argument("POSITIONER_IDS", type=int, nargs=-1, required=False)
@click.option(
    "--start-angle",
    type=float,
    default=START_ANGLE,
    show_default=True,
    help="Angle from which to start the calibration.",
)
@click.option(
    "--skip-phase-1",
    is_flag=True,
    help="Do not run phase 1.",
)
@click.option(
    "-d",
    "--dry-run",
    is_flag=True,
    help="Execute trajectories but do not home the robots.",
)
async def home(
    command: JaegerCommandType,
    fps: FPS,
    axis: str,
    positioner_ids: tuple[int, ...] | list[int] = (),
    start_angle: float = 5.0,
    skip_phase_1: bool = False,
    dry_run: bool = False,
):
    """Re-homes positioner datums in bulk."""

    axis = axis.lower()

    positioner_ids = list(positioner_ids)
    if positioner_ids == []:
        positioner_ids = [
            pos.positioner_id
            for pos in fps.positioners.values()
            if not pos.disabled and not pos.offline
        ]
    else:
        for positioner_id in positioner_ids:
            if positioner_id not in fps.positioners.keys():
                return command.fail(f"Unknown positioner {positioner_id}.")
            if fps[positioner_id].disabled or fps[positioner_id].offline:
                return command.fail(f"Cannot home disabled positioner {positioner_id}.")

    if axis == "alpha":
        result = await check_positions(
            fps,
            beta_min=SAFE_BETA_MIN,
            beta_max=SAFE_BETA_MAX,
            check_disabled=True,
        )
        if not result:
            return command.fail("Beta arms are not safely folded.")

        command.info(f"Moving alpha to {start_angle} degrees.")
        try:
            await fps.goto(
                {
                    pos.positioner_id: (start_angle, pos.beta)
                    for pos in fps.positioners.values()
                    if pos.positioner_id in positioner_ids and pos.beta is not None
                }
            )
        except TrajectoryError as err:
            return command.fail(f"Trajectory failed with error {err}.")

        if dry_run is False:
            command.info("Homing in alpha.")
            home_tasks = []
            for pid in positioner_ids:
                home_tasks.append(fps[pid].home(alpha=True, beta=False))
            await asyncio.gather(*home_tasks)
        else:
            command.warning("Skipping homing in dry run.")

        command.info("Homing complete. Reverting to folded.")
        try:
            await fps.goto(
                {
                    pos.positioner_id: (ALPHA_FOLDED, pos.beta)
                    for pos in fps.positioners.values()
                    if pos.positioner_id in positioner_ids and pos.beta is not None
                },
                go_cowboy=True,
            )
        except TrajectoryError as err:
            return command.fail(f"Trajectory failed with error {err}.")

    elif axis == "beta":
        if not (await fps.is_folded()):
            return command.fail("The FPS is not folded. Cannot home beta.")

        command.info("Creating paths for phase 1.")

        grid1 = get_robot_grid(fps, collision_buffer=COLLISION_BUFFER)
        for robot in grid1.robotDict.values():
            if fps[robot.id].disabled is False:
                robot.setAlphaBeta(ALPHA1, start_angle)

        phase_2_pids = []
        for robot in grid1.robotDict.values():
            if robot.isOffline:
                continue
            if grid1.isCollided(robot.id):
                command.debug(f"Deferring positioner {robot.id} to phase 2.")
                robot.setAlphaBeta(ALPHA1, 180)
                phase_2_pids.append(robot.id)

        # All robots that should be moved in phase 1, even if won't be homed.
        phase_1_pids = [pid for pid in positioner_ids if pid not in phase_2_pids]
        # Robots in phase 1 that will be homed.
        phase_1_home_pids = list(set(positioner_ids) & set(phase_1_pids))

        command.info("Creating paths for phase 2.")

        grid2 = get_robot_grid(fps, collision_buffer=COLLISION_BUFFER)
        for robot in grid2.robotDict.values():
            if robot.id in phase_1_home_pids and dry_run is False:
                robot.betaOffDeg = 0.0
            if robot.id in phase_2_pids:
                robot.setAlphaBeta(ALPHA2, start_angle)
            else:
                robot.setAlphaBeta(ALPHA2, 180)

        _, from_destination_2, failed, _ = get_path_pair(
            grid2,
            path_generation_mode="greedy",
        )
        if failed:
            return command.fail("Failed generating paths for beta homing phase 2.")

        if skip_phase_1:
            phase_1_home_pids = []

        if len(phase_1_home_pids) > 0:
            _, from_destination_1, failed, _ = get_path_pair(
                grid1,
                path_generation_mode="greedy",
            )
            if failed:
                return command.fail("Failed generating paths for beta homing phase 1.")

            try:
                await _home_beta_phase(
                    command,
                    fps,
                    phase_1_home_pids,
                    1,
                    start_angle,
                    from_destination_1,
                    dry_run=dry_run,
                )
            except JaegerError as err:
                return command.fail(f"Phase 1 homing failed with error: {err}")

        else:
            command.info("No selected positioners in phase 1. Skipping ")

        # Robots in phase 2 that will be homed.
        phase_2_home_pids = list(set(positioner_ids) & set(phase_2_pids))

        if len(phase_2_home_pids) > 0:
            try:
                await _home_beta_phase(
                    command,
                    fps,
                    phase_2_home_pids,
                    2,
                    start_angle,
                    from_destination_2,
                    dry_run=dry_run,
                    extra_zero_positioner_ids=phase_1_home_pids,
                )
            except JaegerError as err:
                return command.fail(f"Phase 2 homing failed with error: {err}")

        else:
            command.info("No selected positioners in phase 2. Skipping ")

    return command.finish("Homing complete.")


async def check_positions(
    fps: FPS,
    alpha_min: float | None = None,
    alpha_max: float | None = None,
    beta_min: float | None = None,
    beta_max: float | None = None,
    check_disabled: bool = True,
):
    """Checks that positioners are in the correct place."""

    await fps.update_position()
    positions = fps.get_positions(ignore_disabled=not check_disabled)

    alpha_pos = positions[:, 1]
    if alpha_min is not None and alpha_max is not None:
        if numpy.any(alpha_pos < alpha_min) or numpy.any(alpha_pos > alpha_max):
            return False

    beta_pos = positions[:, 2]
    if beta_min is not None and beta_max is not None:
        if numpy.any(beta_pos < beta_min) or numpy.any(beta_pos > beta_max):
            return False

    return True


async def _home_beta_phase(
    command: JaegerCommandType,
    fps: FPS,
    home_positioner_ids: list[int],
    phase: int,
    start_angle: float,
    from_destination: TrajectoryType,
    dry_run: bool = False,
    extra_zero_positioner_ids: list[int] = [],
):
    command.info(f"Moving robots to phase {phase} initial position.")
    try:
        await fps.send_trajectory(from_destination, command=command)
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error: {err}")

    command.info(f"Starting phase {phase} calibration.")

    command.debug(f"Homing positioners {home_positioner_ids}.")

    if dry_run is False:
        command.info("Homing in beta.")
        home_tasks = []
        for pid in home_positioner_ids:
            home_tasks.append(fps[pid].home(alpha=False, beta=True))
        await asyncio.gather(*home_tasks)
    else:
        command.warning("Skipping homing in dry run.")

    command.info(f"Homing for phase {phase} finished.")
    command.info("Reverting robots to initial angle.")

    try:
        await fps.goto(
            {
                pos.positioner_id: (pos.alpha, start_angle)
                for pos in fps.positioners.values()
                if pos.positioner_id in home_positioner_ids and pos.alpha is not None
            },
            go_cowboy=True,
        )
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error {err}.")

    # Although it's probably ok, we don't want to simply apply the to_destination
    # trajectory because the offsets have changed and that may cause a collision.
    # Instead we create a new grid, manually set the beta offsets to zero, then
    # calculate and execute an unwind.

    command.info("Reverting to folded.")

    await fps.update_position()

    grid_unwind = get_robot_grid(fps, collision_buffer=COLLISION_BUFFER)
    for robot in grid_unwind.robotDict.values():
        if dry_run is False:
            if robot.id in home_positioner_ids or robot.id in extra_zero_positioner_ids:
                robot.betaOffDeg = 0.0
        if robot.isOffline:
            continue
        robot.setAlphaBeta(fps[robot.id].alpha, fps[robot.id].beta)
        robot.setDestinationAlphaBeta(ALPHA_FOLDED, BETA_FOLDED)

    # This is equivalent to unwind with force=True but using the customised
    # grid with zeroed offsets. We want to force-unwind to ensure that most of
    # the robots make it home and there are only a few deadlocked robots that
    # need manual intervention.
    to_destination, _, did_fail, deadlocks = get_path_pair(
        grid_unwind,
        path_generation_mode="greedy",
        ignore_did_fail=True,
        stop_if_deadlock=True,
    )

    if len(deadlocks) > 0:
        command.warning("Deadlocks found in unwind trajectory but will unwind.")

    try:
        await fps.send_trajectory(to_destination, command=command)
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error: {err}")

    if len(deadlocks) > 0:
        command.warning(f"Robots that have been homed in beta: {home_positioner_ids}")
        raise JaegerError(
            f"Failed to unwind robots {deadlocks}. Remove the beta offsets for the "
            "homed robots in fps_calibrations, then try to manually unwind."
        )
