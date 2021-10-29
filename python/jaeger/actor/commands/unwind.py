#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-21
# @Filename: unwind.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
from functools import partial

from typing import TYPE_CHECKING

import click

from jaeger.design import unwind_or_explode
from jaeger.exceptions import TrajectoryError

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["unwind", "explode"]


@jaeger_parser.command()
@click.option("--connected", is_flag=True, help="Unwind only connected positioners.")
async def unwind(
    command: Command[JaegerActor],
    fps: FPS,
    connected: bool = False,
):
    """Sends the FPS to folded."""

    command.debug(text="Calculating unwind trajectory.")

    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        func = partial(
            unwind_or_explode,
            positions,
            only_connected=connected,
        )
        trajectory = await asyncio.get_event_loop().run_in_executor(None, func)
    except ValueError as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    if len(set(trajectory.keys()) - set(positions.keys())) > 0:
        # Some expected positioners are not connected.
        if connected:
            command.warning(text="Unwinding only connected positioners!")
            trajectory = {k: trajectory[k] for k in trajectory if k in positions}
        else:
            return command.fail(
                error="The unwind trajectory contains more positioners than those "
                "connected. You can use --connected if you know what you are doing."
            )

    command.info("Executing unwind trajectory.")

    try:
        await fps.send_trajectory(trajectory, use_sync_line=False)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")


@jaeger_parser.command()
@click.argument("EXPLODE-DEG", type=float)
@click.option("--connected", is_flag=True, help="Explode only connected positioners.")
async def explode(
    command: Command[JaegerActor],
    fps: FPS,
    explode_deg: float,
    connected: bool = False,
):
    """Explodes the FPS."""

    command.debug(text="Calculating explode trajectory.")

    positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.positioners.values()}

    try:
        func = partial(
            unwind_or_explode,
            positions,
            only_connected=connected,
            explode=True,
            explode_deg=explode_deg,
        )
        trajectory = await asyncio.get_event_loop().run_in_executor(None, func)
    except ValueError as err:
        return command.fail(error=f"Failed calculating trajectory: {err}")

    if len(set(trajectory.keys()) - set(positions.keys())) > 0:
        # Some expected positioners are not connected.
        if connected:
            command.warning(text="Exploding only connected positioners!")
            trajectory = {k: trajectory[k] for k in trajectory if k in positions}
        else:
            return command.fail(
                error="The explode trajectory contains more positioners than those "
                "connected. You can use --connected if you know what you are doing."
            )

    command.info("Executing explode trajectory.")

    try:
        await fps.send_trajectory(trajectory, use_sync_line=False)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
