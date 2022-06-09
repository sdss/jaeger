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
from jaeger.kaiju import get_path_pair, get_robot_grid

from . import jaeger_parser


if TYPE_CHECKING:
    from jaeger import FPS

    from . import JaegerCommandType


__all__ = ["home"]


SAFE_BETA_MIN: float = 165.0
SAFE_BETA_MAX: float = 180.0

START_ANGLE: float = 5.0
COLLISION_BUFFER: float = 3.2

ALPHA1: float = 115
ALPHA2: float = 52


@jaeger_parser.command()
@click.argument(
    "AXIS",
    type=click.Choice(["alpha", "beta"], case_sensitive=False),
)
@click.argument("POSITIONER_IDS", nargs=-1, required=False)
@click.option(
    "--start-angle",
    type=float,
    default=START_ANGLE,
    show_default=True,
    help="Angle from which to start the calibration.",
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
    dry_run: bool = False,
):
    """Re-homes positioner datums in bulk."""

    axis = axis.lower()

    alpha0, beta0 = config["kaiju"]["lattice_position"]

    positioner_ids = list(positioner_ids)
    if positioner_ids == []:
        positioner_ids = [
            pos.positioner_id
            for pos in fps.positioners.values()
            if not pos.disabled and not pos.offline
        ]
    else:
        for positioner_id in positioner_ids:
            if positioner_id not in fps.positioners:
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
                    pos.positioner_id: (alpha0, pos.beta)
                    for pos in fps.positioners.values()
                    if pos.positioner_id in positioner_ids and pos.beta is not None
                }
            )
        except TrajectoryError as err:
            return command.fail(f"Trajectory failed with error {err}.")

    elif axis == "beta":

        if not (await fps.is_folded()):
            return command.fail("The FPS is not folded. Cannot home beta.")

        command.info("Creating paths for phase 1.")

        grid1 = get_robot_grid(fps, collision_buffer=COLLISION_BUFFER)
        for robot in grid1.values():
            if fps[robot.id].disabled is False:
                robot.setAlphaBeta(ALPHA1, start_angle)

        phase_2_pids = []
        for robot in grid1.robotDict.values():
            if robot.isOffline:
                continue
            if grid1.isCollided(robot.id):
                command.debug(f"Deferring positioner {robot.id} for phase 2.")
                robot.setAlphaBeta(ALPHA1, 180)
                phase_2_pids.append(robot.id)

        to_destination_1, from_destination_1, failed, _ = get_path_pair(
            grid1,
            path_generation_mode="greedy",
        )
        if failed:
            return command.fail("Failed generating paths for beta homing phase 1.")

        phase_1_pids = [pid for pid in positioner_ids if pid not in phase_2_pids]

        try:
            await _home_beta_phase(
                command,
                fps,
                phase_1_pids,
                1,
                start_angle,
                from_destination_1,
                to_destination_1,
                dry_run=dry_run,
            )
        except JaegerError as err:
            return command.fail(f"Phase 1 homing failed with error: {err}")

        command.info("Creating paths for phase 2.")

        grid2 = get_robot_grid(fps, collision_buffer=COLLISION_BUFFER)
        for robot in grid2.values():
            if robot.id in phase_2_pids:
                robot.setAlphaBeta(ALPHA2, start_angle)
            else:
                robot.setAlphaBeta(ALPHA2, 180)

        to_destination_2, from_destination_2, failed, _ = get_path_pair(
            grid2,
            path_generation_mode="greedy",
        )
        if failed:
            return command.fail("Failed generating paths for beta homing phase 2.")

        try:
            await _home_beta_phase(
                command,
                fps,
                phase_2_pids,
                2,
                start_angle,
                from_destination_2,
                to_destination_2,
                dry_run=dry_run,
            )
        except JaegerError as err:
            return command.fail(f"Phase 2 homing failed with error: {err}")

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
    positioner_ids: list[int],
    phase: int,
    start_angle: float,
    from_destination: dict,
    to_destination: dict,
    dry_run: bool = False,
):

    command.info(f"Moving robots to phase {phase} initial position.")
    try:
        await fps.send_trajectory(from_destination, command=command)
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error: {err}")

    command.info(f"Starting phase {phase} calibration.")

    command.debug(f"Homing positioners {positioner_ids}.")

    if dry_run is False:
        command.info("Homing in beta.")
        home_tasks = []
        for pid in positioner_ids:
            home_tasks.append(fps[pid].home(alpha=False, beta=True))
        await asyncio.gather(*home_tasks)
    else:
        command.warning("Skipping homing in dry run.")

    command.info(f"Homing for phase {phase} finished.")
    command.info("Reverting robots to starting angle.")

    try:
        await fps.goto(
            {
                pos.positioner_id: (pos.alpha, start_angle)
                for pos in fps.positioners.values()
                if pos.positioner_id in positioner_ids and pos.alpha is not None
            }
        )
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error {err}.")

    command.info("Reverting to folded.")

    try:
        await fps.send_trajectory(to_destination, command=command)
    except TrajectoryError as err:
        raise JaegerError(f"Trajectory failed with error: {err}")
