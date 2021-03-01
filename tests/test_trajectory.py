#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_send_trajectory(vfps):

    await vfps.initialise()

    await vfps.send_trajectory(
        {1: {"alpha": [(1, 1), (2, 2)], "beta": [(1, 1), (2, 2)]}}, use_sync_line=False
    )

    await vfps.update_position()
