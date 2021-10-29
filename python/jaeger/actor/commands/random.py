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
from jaeger.design import get_robot_grid
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

    command.debug(text="Creating robot grid.")

    seed = seed or numpy.random.randint(0, 1000000)
    numpy.random.seed(seed)

    robot_grid = get_robot_grid(seed=seed)

    command.debug(text="Checking that all positioners are folded.")

    # Check that all positioners are folded.
    alphaL, betaL = config["kaiju"]["lattice_position"]
    await fps.update_position()
    if not numpy.allclose(fps.get_positions()[:, 1:] - [alphaL, betaL], 0, atol=0.1):
        return command.fail(error="Not all the positioners are folded.")

    command.info(text="Calculating random trajectory.")

    for robot in robot_grid.robotDict.values():
        robot.setDestinationAlphaBeta(alphaL, betaL)

        if uniform is not None:
            try:
                alpha0, alpha1, beta0, beta1 = map(float, uniform.split(","))
                alpha = numpy.random.uniform(alpha0, alpha1)
                beta = numpy.random.uniform(beta0, beta1)
                robot.setAlphaBeta(alpha, beta)
            except Exception as err:
                return command.fail(error=f"Failed calculating uniform ranges: {err}")

        else:
            if safe:
                safe_mode = config["safe_mode"]
                if safe_mode is False:
                    safe_mode = {"min_beta": 160, "max_beta": 220}

                alpha = numpy.random.uniform(0, 359.9)
                beta = numpy.random.uniform(
                    safe_mode["min_beta"],
                    safe_mode["max_beta"],
                )

                robot.setAlphaBeta(alpha, beta)
            else:
                robot.setXYUniform()

    robot_grid.decollideGrid()
    robot_grid.pathGenGreedy()

    speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]
    forward, _ = robot_grid.getPathPair(speed=speed)

    command.info("Executing random trajectory.")

    try:
        await fps.send_trajectory(forward)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
