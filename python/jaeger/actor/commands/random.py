#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-29
# @Filename: unwind.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import numpy

from jaeger import config
from jaeger.design import ManualConfiguration, get_robot_grid
from jaeger.exceptions import TrajectoryError

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["random"]


@jaeger_parser.command()
@click.argument("SEED", type=int, required=False)
@click.option("--safe", is_flag=True, help="Limit beta to a safe range.")
@click.option(
    "--uniform",
    type=str,
    default=None,
    help="Comma-separated alpha and beta ranges.",
)
async def random(
    command: Command[JaegerActor],
    fps: FPS,
    seed: int | None = None,
    safe: bool = False,
    uniform: str | None = None,
):
    """Executes a random, valid configuration."""

    if safe is True and uniform is not None:
        return command.fail(error="--safe and --uniform are mutually exclusive.")

    command.debug(text="Checking that all positioners are folded.")

    # Check that all positioners are folded.
    await fps.update_position()
    positions = fps.get_positions()

    if len(positions) == 0:
        return command.fail("No positioners connected")

    alphaL, betaL = config["kaiju"]["lattice_position"]
    if not numpy.allclose(positions[:, 1:] - [alphaL, betaL], 0, atol=0.1):
        return command.fail(error="Not all the positioners are folded.")

    command.info(text="Creating random configuration.")

    uniform_unpack: tuple[float, ...] | None
    if uniform:
        try:
            uniform_unpack = tuple(map(float, uniform.split(",")))
        except Exception as err:
            return command.fail(error=f"Failed calculating uniform ranges: {err}")
    else:
        uniform_unpack = None

    configuration = ManualConfiguration.create_random(
        seed=seed,
        uniform=uniform_unpack,
        safe=safe,
    )
    trajectory = configuration.get_trajectory(simple_decollision=True)

    command.info("Executing random trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
