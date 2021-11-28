#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-21
# @Filename: unwind.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.kaiju import explode, unwind

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["unwind_command", "explode_command"]


@jaeger_parser.command(name="unwind")
@click.option(
    "--collision-buffer",
    type=click.FloatRange(1.6, 3.0),
    help="Custom collision buffer",
)
@click.option(
    "--force",
    is_flag=True,
    help="Execute unwind even in presence of deadlocks.",
)
async def unwind_command(
    command: Command[JaegerActor],
    fps: FPS,
    collision_buffer: float | None = None,
    force: bool = False,
):
    """Sends the FPS to folded."""

    command.debug(text="Calculating unwind trajectory.")

    await fps.update_position()
    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        trajectory = await unwind(
            positions,
            collision_buffer=collision_buffer,
            force=force,
        )
    except (ValueError, TrajectoryError) as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    command.info("Executing unwind trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their destinations.")


@jaeger_parser.command(name="explode")
@click.argument("EXPLODE-DEG", type=float)
async def explode_command(command: Command[JaegerActor], fps: FPS, explode_deg: float):
    """Explodes the FPS."""

    command.debug(text="Calculating explode trajectory.")

    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        trajectory = await explode(positions, explode_deg=explode_deg)
    except (JaegerError, ValueError, TrajectoryError) as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    command.info("Executing explode trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their destinations.")
