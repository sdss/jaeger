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
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
)

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
    check_full_range: bool = False,
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
    check_full_range
        If `True`, the script will check that the robots can move to the full range
        of alpha and beta values. After the moves have been executed, each robot is
        sequentially sent to (0, 0) and to (360, 180).

    """

    log.sh.setLevel(20)  # INFO

    console = log.rich_console
    assert console

    log.info("Initialising FPS.")
    fps = await FPS().initialise(
        start_pollers=False,
        check_low_temperature=False,
        keep_disabled=False,
        skip_assignments_check=True,
    )

    if fps.locked:
        log.warning("FPS is locked. Unlocking all robots.")
        await fps.unlock()

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

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        console=console,
        transient=True,
    )

    if n_moves > 0:
        if sequential:
            log.info(f"Moving robots sequentially: {robot_ids}")

            for pid in robot_ids:
                log.warning(f"Testing robot {pid}")

                alphas = numpy.random.uniform(alpha_range[0], alpha_range[1], n_moves)
                betas = numpy.random.uniform(beta_range[0], beta_range[1], n_moves)

                log.info(f"Folding robot {pid}")
                await fps.goto({pid: (10, 170)}, go_cowboy=True, save_snapshot=False)

                with progress:
                    task = progress.add_task(
                        f"Moving robot {pid}",
                        total=n_moves,
                    )

                    for ii in range(n_moves):
                        alpha = alphas[ii]
                        beta = betas[ii]
                        log.info(f"Moving to ({alpha:.2f}, {beta:.2f})")
                        await fps.goto(
                            {pid: (alpha, beta)},
                            go_cowboy=True,
                            save_snapshot=False,
                        )
                        progress.update(task, advance=1)
                        await asyncio.sleep(2)

                    log.info(f"Re-folding robot {pid}.")
                    await fps.goto(
                        {pid: (10, 170)},
                        go_cowboy=True,
                        save_snapshot=False,
                    )

        else:
            log.info("Folding robots.")
            await fps.goto(
                {pid: (10, 170) for pid in robot_ids},
                go_cowboy=True,
                save_snapshot=False,
            )

            alphas = numpy.random.uniform(alpha_range[0], alpha_range[1], n_moves)
            betas = numpy.random.uniform(beta_range[0], beta_range[1], n_moves)

            log.warning(f"Moving all robots: {robot_ids}")

            with progress:
                task = progress.add_task(
                    "Moving robots",
                    total=n_moves,
                )
                for ii in range(n_moves):
                    alpha = alphas[ii]
                    beta = betas[ii]
                    log.info(f"Moving to ({alpha:.2f}, {beta:.2f})")
                    await fps.goto(
                        {pid: (alpha, beta) for pid in robot_ids},
                        go_cowboy=True,
                        save_snapshot=False,
                    )
                    progress.update(task, advance=1)
                    await asyncio.sleep(2)

                log.info("Re-folding robots.")
                await fps.goto(
                    {pid: (10, 170) for pid in robot_ids},
                    go_cowboy=True,
                    save_snapshot=False,
                )
    else:
        log.info("Folding robots.")
        await fps.goto(
            {pid: (10, 170) for pid in robot_ids},
            go_cowboy=True,
            save_snapshot=False,
        )

        log.warning("Skipping moves.")

    progress.stop()

    if check_full_range:
        log.warning("Checking full range of alpha and beta values.")

        for ii, pid in enumerate(robot_ids):
            log.info(f"Testing robot {pid} ({ii + 1}/{len(robot_ids)})")

            log.info(f"Moving robot {pid} to (0, 0).")
            await fps.goto({pid: (0, 0)}, go_cowboy=True, save_snapshot=False)
            await asyncio.sleep(2)

            log.info(f"Moving robot {pid} to (360, 180).")
            await fps.goto({pid: (360, 180)}, go_cowboy=True, save_snapshot=False)
            await asyncio.sleep(2)

            log.info(f"Re-folding robot {pid}.")
            await fps.goto({pid: (10, 170)}, go_cowboy=True, save_snapshot=False)


if __name__ == "__main__":
    asyncio.run(
        test_robots_sextant(
            n_moves=100,
            disabled_robots=[],
            sequential=False,
            alpha_range=(5, 355),
            beta_range=(5, 175),
            check_full_range=True,
        )
    )
