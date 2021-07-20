#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-18
# @Filename: send_trajectory_alpha.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import sys

import numpy

from jaeger import log
from jaeger.fps import FPS


log.sh.setLevel(20)


async def main():

    fps = await FPS.create()

    assert len(fps) > 0, "No positioners connected."

    n_points = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    alpha_points = numpy.linspace(0, 180, n_points + 1)
    beta_points = numpy.linspace(180, 180, n_points + 1)
    t_points = numpy.linspace(1, 30, n_points + 1)

    alpha = list(zip(alpha_points, t_points))
    beta = list(zip(beta_points, t_points))

    log.info("Going to start position.")
    await asyncio.gather(*[fps[pid].goto(0, 180) for pid in fps])

    log.info("Sending trajectory.")
    await fps.send_trajectory(
        {pid: {"alpha": alpha, "beta": beta} for pid in fps},
        use_sync_line=False,
    )


asyncio.run(main())
