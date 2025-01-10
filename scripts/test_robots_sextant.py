#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2025-01-06
# @Filename: test_robots_sextant.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)


from __future__ import annotations

import asyncio
import pathlib

from typing import Sequence

import numpy
from rich.progress import track

from jaeger import FPS, config, log


ROOT = pathlib.Path(__file__).parent / ".."
CONFIG_FILE = ROOT / "src/jaeger/etc/sextants/jaeger_sextant.yml"
config.load(CONFIG_FILE)


async def test_robots_sextant(
    n_moves: int = 25,
    sequential: bool = False,
    disabled_robots: Sequence[int] = [],
    alpha_range: Sequence[float] = [10, 350],
    beta_range: Sequence[float] = [10, 170],
):
    """This script runs random moves in robots connected to a sextant controller.

    Parameters
    ----------
    n_moves
        The number of moves to execute.
    sequential
        Whether to move robots sequentially, all at a time, or all together.
    disabled_robots
        A list of robots to disable and ignore.
    alpha_range
        The range of alpha values to use.
    beta_range
        The range of beta values to use.

    """

    log.sh.setLevel(20)  # INFO

    console = log.rich_console
    assert console

    fps = await FPS().initialise(
        start_pollers=False,
        check_low_temperature=False,
        keep_disabled=False,
        skip_assignments_check=True,
    )

    for disabled_robot in disabled_robots:
        if disabled_robot in fps:
            fps.pop(disabled_robot)
            fps.disabled.add(disabled_robot)

    robot_ids = list(sorted(fps.keys()))

    log.info("Robot status:")
    for pid in robot_ids:
        robot = fps[pid]
        firmware = robot.firmware
        status = robot.status.value
        alpha = robot.alpha
        beta = robot.beta
        log.info(
            f"Robot {pid:04d} - firmware {firmware}; status 0x{status:02X}; "
            f"alpha {alpha:.2f}; beta {beta:.2f}"
        )

    # await fps.goto({pid: (10, 170) for pid in robot_ids}, go_cowboy=True)
    # return

    if sequential:
        log.info(f"Moving robots sequentially: {robot_ids}")

        for pid in robot_ids:
            alphas = numpy.random.uniform(alpha_range[0], alpha_range[1], n_moves)
            betas = numpy.random.uniform(beta_range[0], beta_range[1], n_moves)

            log.info(f"Folding robot {pid}")
            await fps.goto({pid: (10, 170)}, go_cowboy=True)

            for ii in track(
                range(n_moves),
                description=f"Moving robot {pid}",
                console=console,
            ):
                log.info(f"Moving robot {pid} to ({alphas[ii]:.2f}, {betas[ii]:.2f})")
                await fps.goto({pid: (alphas[ii], betas[ii])}, go_cowboy=True)
                await asyncio.sleep(2)

            log.info(f"Re-folding robot {pid}.")
            await fps.goto({pid: (10, 170)}, go_cowboy=True)

    else:
        log.info("Folding robots.")
        await fps.goto({pid: (10, 170) for pid in robot_ids}, go_cowboy=True)

        alphas = numpy.random.uniform(alpha_range[0], alpha_range[1], n_moves)
        betas = numpy.random.uniform(beta_range[0], beta_range[1], n_moves)

        log.info(f"Moving all robots: {robot_ids}")
        for ii in track(range(n_moves), description="Moving robots", console=console):
            log.info(f"Moving robots to ({alphas[ii]:.2f}, {betas[ii]:.2f})")
            await fps.goto(
                {pid: (alphas[ii], betas[ii]) for pid in robot_ids},
                go_cowboy=True,
            )
            await asyncio.sleep(2)

        log.info("Re-folding robots.")
        await fps.goto({pid: (10, 170) for pid in robot_ids}, go_cowboy=True)


if __name__ == "__main__":
    asyncio.run(
        test_robots_sextant(
            n_moves=50,
            disabled_robots=[],
            sequential=False,
            alpha_range=(20, 350),
            beta_range=(20, 170),
        )
    )
