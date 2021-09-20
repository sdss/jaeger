#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio

import pytest

from jaeger import config
from jaeger.exceptions import PositionerError
from jaeger.maskbits import PositionerStatus


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_get_position(vfps, vpositioners):

    vpositioners[1].position = (90, 90)

    await vfps.initialise()
    await asyncio.sleep(0.1)

    assert vfps[1].position == (90.0, 90.0)

    vpositioners[1].position = (180, 180)

    await vfps[1].update_position()

    assert vfps[1].position == (180.0, 180.0)


@pytest.mark.parametrize("use_trajectory", (True, False))
async def test_goto(vfps, event_loop, use_trajectory):

    await vfps.initialise()

    assert await vfps[1].goto(1, 1, use_trajectory=use_trajectory)


@pytest.mark.parametrize("use_trajectory", (True, False))
async def test_goto_relative(vfps, event_loop, use_trajectory):

    await vfps.initialise()

    assert await vfps[1].goto(1, 1, relative=True, use_trajectory=use_trajectory)


@pytest.mark.parametrize("use_trajectory", (True, False))
async def test_goto_safe_mode(vfps, monkeypatch, use_trajectory):
    monkeypatch.setitem(config, "safe_mode", True)

    await vfps.initialise()

    with pytest.raises(Exception) as err:
        await vfps[1].goto(100, 150, use_trajectory=use_trajectory)
        "safe mode is on" in str(err)


@pytest.mark.parametrize("use_trajectory", (True, False))
async def test_goto_safe_mode_custom_beta(vfps, monkeypatch, use_trajectory):
    monkeypatch.setitem(config, "safe_mode", {"min_beta": 170})

    await vfps.initialise()

    with pytest.raises(Exception) as err:
        await vfps[1].goto(100, 169, use_trajectory=use_trajectory)
        "safe mode is on" in str(err)


async def test_get_bus(vfps):

    pos = vfps[1]
    assert pos.get_bus() == (0, None)


async def test_bus_multibus(vfps):

    vfps.can.multibus = True
    vfps.positioner_to_bus[1] = (vfps.can.interfaces[0], None)

    pos = vfps[1]
    assert pos.get_bus() == (0, None)


async def test_bus_no_fps(vfps):

    pos = vfps[1]
    pos.fps = None

    with pytest.raises(PositionerError):
        pos.get_bus()


async def test_home(vfps):

    pos = vfps[1]
    assert (await pos.home()) is None


async def test_home_moving(vfps):

    pos = vfps[1]
    pos.status = PositionerStatus.ESTIMATED_POSITION  # Just not DISPLACEMENT_COMPLETED

    with pytest.raises(PositionerError):
        await pos.home()


async def test_home_no_fps(vfps):

    pos = vfps[1]
    pos.fps = None

    with pytest.raises(PositionerError):
        await pos.home()


@pytest.mark.parametrize("motor", ["alpha", "beta", "both"])
@pytest.mark.parametrize("loop", ["open", "closed"])
@pytest.mark.parametrize("collisions", [True, False])
async def test_set_loop(vfps, motor, loop, collisions):

    pos = vfps[1]
    assert (await pos.set_loop(motor=motor, loop=loop, collisions=collisions)) is True


@pytest.mark.parametrize("mode", [True, False])
@pytest.mark.parametrize("alpha", [True, False])
@pytest.mark.parametrize("beta", [True, False])
async def test_set_precise_move(vfps, mode, alpha, beta):

    pos = vfps[1]

    if alpha or beta:
        assert (await pos.set_precise_move(mode, alpha, beta)) is True
    else:
        with pytest.raises(PositionerError):
            await pos.set_precise_move(mode, alpha, beta)


async def test_get_number_trajectories(vfps):

    pos = vfps[1]
    pos.firmware = "04.01.21"

    assert (await pos.get_number_trajectories()) == 1


@pytest.mark.parametrize("fw", [None, "04.01.20"])
async def test_get_number_trajectories_bad_firmware(vfps, fw):

    pos = vfps[1]
    pos.firmware = fw

    assert (await pos.get_number_trajectories()) is None
