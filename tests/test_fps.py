#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-23
# @Filename: test_fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import pytest

from drift import Relay

import jaeger
from jaeger.exceptions import JaegerError
from jaeger.maskbits import PositionerStatus
from jaeger.testing import VirtualFPS


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_vfps(vfps):

    assert isinstance(vfps, VirtualFPS)


async def test_get_id(vfps, vpositioners):

    command = await vfps.send_command("GET_ID", n_positioners=len(vpositioners))
    assert len(command.replies) == len(vpositioners)


async def test_initialise(vfps, vpositioners):

    await vfps.initialise()
    await asyncio.sleep(0.1)  # Give some time for the poller to set position.

    assert len(vfps) == len(vpositioners)

    positioner1 = vfps[1]

    motor_speed = jaeger.config["positioner"]["motor_speed"]
    assert positioner1.speed == (motor_speed, motor_speed)

    assert positioner1.position == (0.0, 0.0)

    assert positioner1.firmware == "10.11.12"


async def test_pollers(vfps):

    await vfps.initialise()

    assert vfps.pollers.status.name == "status"
    assert vfps.pollers.position.name == "position"

    assert vfps.pollers["status"].name == "status"
    assert vfps.pollers["position"].name == "position"

    assert vfps.pollers.status.running
    assert vfps.pollers.position.running


async def test_stop_pollers(vfps):

    await vfps.initialise()
    await vfps.pollers.stop()

    assert not vfps.pollers.status.running
    assert not vfps.pollers.position.running


async def test_pollers_delay(vfps, vpositioners):

    await vfps.initialise()
    await vfps.pollers.set_delay(0.01, immediate=True)

    vpositioners[1].status |= PositionerStatus.HALL_ALPHA_DISABLE
    vpositioners[1].position = (180.0, 180.0)

    await asyncio.sleep(0.1)

    assert vfps[1].position == (180.0, 180.0)
    assert PositionerStatus.HALL_ALPHA_DISABLE in vpositioners[1].status

    await vfps.pollers.stop()


async def test_shutdown(vfps):

    await vfps.shutdown()


async def test_ieb(vfps):

    sync = vfps.ieb.get_device("DO1.SYNC")
    assert isinstance(sync, Relay)

    assert (await sync.read())[0] == "open"


async def test_positioner_disabled_send_command_fails_broadcast(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    with pytest.raises(JaegerError) as err:
        await vfps.send_command("START_TRAJECTORY", positioner_id=0)

    assert "Some positioners are disabled." in str(err)


async def test_positioner_disabled_send_command_fails(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    with pytest.raises(JaegerError) as err:
        await vfps.send_command("GO_TO_ABSOLUTE_POSITION", positioner_id=2)

    assert "Positioner 2 is disabled." in str(err)


async def test_positioner_disabled_send_to_all(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    results = await vfps.send_to_all("GET_ID", positioners=0)
    assert len(results) == len(vfps) - 1
