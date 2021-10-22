#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-21
# @Filename: unwind.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio

from typing import TYPE_CHECKING

from jaeger.design import unwind as unwind_func
from jaeger.exceptions import TrajectoryError

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor
    from jaeger.fps import FPS


__all__ = ["unwind"]


@jaeger_parser.command()
async def unwind(command: Command[JaegerActor], fps: FPS):
    """Sends the FPS to folded."""

    command.debug(text="Calculating unwind trajectory.")

    current_positions = {p.id: (p.alpha, p.beta) for p in fps.positioners.values()}
    trajectory = await asyncio.get_event_loop().run_in_executor(
        None,
        unwind_func,
        fps.configuration,
        current_positions,
    )

    command.info("Executing unwind trajectory.")

    try:
        await fps.send_trajectory(trajectory)
    except TrajectoryError as err:
        command.fail(error=f"Trajectory failed with error: {err}")

    command.finish(text="All positioners reached their new positions.")
