#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2025-01-10
# @Filename: loop_random_configurations.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
from time import time

import numpy
from rich.prompt import Confirm

from jaeger import FPS, config, log
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.target.tools import create_random_configuration


SEED: int = 42


async def loop_random_configurations():
    """Runs a loop of random configurations."""

    DANGER: bool = True
    N_CONFIGURATIONS: int = 20
    COLLISION_BUFFER: float = 3.2

    INTERACTIVE: bool = False

    log.sh.setLevel(20)
    console = log.rich_console

    alphaL: float
    betaL: float

    # Should always be 10, 170 but just in case.
    alphaL, betaL = config["kaiju"]["lattice_position"]

    fps = await FPS().initialise()

    for nn in range(N_CONFIGURATIONS):
        log.warning(f"Random configuration {nn+1}/{N_CONFIGURATIONS}")

        if not await fps.is_folded():
            raise RuntimeError("The array is not folded.")

        # This is an additional check. Should be the same as is_folded() but
        # just copying the code from the command.
        await fps.update_position()
        positions = fps.get_positions(ignore_disabled=True)

        if not numpy.allclose(positions[:, 1:] - [alphaL, betaL], 0, atol=1):
            raise JaegerError("Not all the positioners are folded.")

        log.info("Creating random configuration.")

        try:
            t0 = time()

            configuration = await create_random_configuration(
                fps,
                seed=SEED,
                uniform=None,
                safe=not DANGER,
                collision_buffer=COLLISION_BUFFER,
                max_retries=100,
                sea_anemone=False,
            )
        except JaegerError as err:
            raise JaegerError(f"jaeger random failed: {err}")
        else:
            log.info(f"Configuration created in {time() - t0:.2f} seconds.")

        # Make this the FPS configuration
        fps.configuration = configuration

        if INTERACTIVE:
            if not Confirm.ask("Do you want to execute?", console=console):
                break

        log.info("Executing random trajectory.")

        try:
            await fps.send_trajectory(configuration.from_destination, dump=False)
        except TrajectoryError as err:
            raise JaegerError(f"Trajectory failed with error: {err}")

        configuration.executed = True

        if INTERACTIVE:
            if not Confirm.ask("Do you want to revert?", console=console):
                break

        log.info("Reverting to original configuration.")
        await fps.send_trajectory(configuration.to_destination, dump=False)


if __name__ == "__main__":
    asyncio.run(loop_random_configurations())
