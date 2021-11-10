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

from jaeger.design import explode, unwind
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.utils import run_in_executor

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["unwind_command", "explode_command"]


@jaeger_parser.command(name="unwind")
async def unwind_command(command: Command[JaegerActor], fps: FPS):
    """Sends the FPS to folded."""

    command.debug(text="Calculating unwind trajectory.")

    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        trajectory = await run_in_executor(unwind, positions)
    except ValueError as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    command.info("Executing unwind trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")


@jaeger_parser.command(name="explode")
@click.argument("EXPLODE-DEG", type=float)
async def explode_command(command: Command[JaegerActor], fps: FPS, explode_deg: float):
    """Explodes the FPS."""

    command.debug(text="Calculating explode trajectory.")

    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        trajectory = await run_in_executor(explode, positions, explode_deg=explode_deg)
    except (JaegerError, ValueError) as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    command.info("Executing explode trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
