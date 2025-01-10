#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2025-01-10
# @Filename: move_to_beta_90.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from rich.prompt import Confirm

from jaeger import FPS, log
from jaeger.target.configuration import ManualConfiguration
from jaeger.target.tools import get_wok_data


async def move_to_beta_90():
    """Moves all the robots to alpha=0, beta=90 without offsets."""

    log.sh.setLevel(20)

    ALPHA: float = 0
    BETA: float = 90

    fps = await FPS().initialise()

    if not await fps.is_folded():
        raise RuntimeError("The array is not folded. Cannot move to beta=90.")

    wok_data = get_wok_data("LCO")

    pos_coords: dict[int, list[float]] = {}
    for row_data in wok_data.to_dicts():
        pid = row_data["positionerID"]
        alpha_off = row_data["alphaOffset"]
        beta_off = row_data["betaOffset"]

        pos_coords[pid] = [ALPHA - alpha_off, BETA - beta_off]

    pos_coords[836][0] -= 3
    pos_coords[933][1] -= 3

    conf = ManualConfiguration.create_from_positions(
        "LCO",
        pos_coords,  # type: ignore
        fps=fps,
    )

    await conf.get_paths()

    log.info(f"Moving to alpha={ALPHA}, beta={BETA}")
    await fps.send_trajectory(conf.from_destination)

    if not Confirm.ask("Do you want to revert the configuration?"):
        await fps.shutdown()
        return

    log.info("Reverting to original configuration.")
    await fps.send_trajectory(conf.to_destination)

    assert await fps.is_folded(), "The array is not folded."

    await fps.shutdown()
    return


if __name__ == "__main__":
    asyncio.run(move_to_beta_90())
