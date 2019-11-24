#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import pytest


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures('vpositioners'), pytest.mark.asyncio]


async def test_get_position(vfps, vpositioners):

    vpositioners[0].position = (90, 90)

    await vfps.initialise()
    await asyncio.sleep(0.1)

    assert vfps[1].position == (90., 90.)

    vpositioners[0].position = (180, 180)

    await vfps[1].update_position()

    assert vfps[1].position == (180., 180.)
