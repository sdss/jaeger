#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-23
# @Filename: test_fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import pytest

import jaeger
from jaeger.testing import VirtualFPS


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures('vpositioners'), pytest.mark.asyncio]


async def test_vfps(vfps):

    assert isinstance(vfps, VirtualFPS)


async def test_get_id(vfps, vpositioners):

    command = await vfps.send_command('GET_ID', n_positioners=len(vpositioners))
    assert len(command.replies) == len(vpositioners)


async def test_initialise(vfps, vpositioners):

    await vfps.initialise()
    await asyncio.sleep(0.1)  # Give some time for the poller to set position.

    assert len(vfps) == len(vpositioners)

    positioner1 = vfps[1]

    motor_speed = jaeger.config['positioner']['motor_speed']
    assert positioner1.speed == (motor_speed, motor_speed)

    assert positioner1.position == (0.0, 0.0)

    assert positioner1.firmware == '10.11.12'
