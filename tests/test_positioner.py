#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import logging

import pytest

from jaeger import config
from jaeger.exceptions import PositionerError


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_get_position(vfps, vpositioners):

    vpositioners[0].position = (90, 90)

    await vfps.initialise()
    await asyncio.sleep(0.1)

    assert vfps[1].position == (90.0, 90.0)

    vpositioners[0].position = (180, 180)

    await vfps[1].update_position()

    assert vfps[1].position == (180.0, 180.0)


async def test_goto(vfps, event_loop):

    await vfps.initialise()

    assert await vfps[1].goto(1, 1)


async def test_goto_no_move(vfps, event_loop, caplog):

    caplog.set_level(logging.INFO)

    await vfps.initialise()

    assert await vfps[1].goto(0, 0)

    assert "did not move" in caplog.records[-1].message


async def test_goto_relative(vfps, event_loop):

    await vfps.initialise()

    assert await vfps[1].goto(1, 1, relative=True)


async def test_goto_safe_mode(vfps, monkeypatch):
    monkeypatch.setitem(config, "safe_mode", True)

    await vfps.initialise()

    with pytest.raises(PositionerError):
        await vfps[1].goto(100, 150)


async def test_goto_safe_mode_custom_beta(vfps, monkeypatch):
    monkeypatch.setitem(config, "safe_mode", {"min_beta": 170})

    await vfps.initialise()

    with pytest.raises(PositionerError):
        await vfps[1].goto(100, 169)
