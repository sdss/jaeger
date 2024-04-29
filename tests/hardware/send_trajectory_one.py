#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-18
# @Filename: send_trajectory_one.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

from jaeger.fps import FPS


async def main():
    fps = await FPS.create()

    assert 20 in fps and len(fps) == 1, "this test requires only positioner 20."

    print("Going to start position.")
    await fps[20]._goto_position(90, 180)

    print("Sending trajectory.")
    await fps.send_trajectory(
        {20: {"alpha": [(90, 0), (0, 20)], "beta": [(180, 0), (170, 15)]}},
        use_sync_line=False,
    )


asyncio.run(main())
