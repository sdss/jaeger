#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-03-19
# @Filename: test_ieb.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest


pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_power_on(actor):
    command = await actor.invoke_mock_command("power on")
    assert command.status.did_succeed


async def test_power_on_fails(actor, mocker):
    # Open PS1 and make it do nothing when it closes.
    ps1 = actor.fps.ieb.get_device("DO1.PS1")
    await ps1.open()
    mocker.patch.object(ps1, "write")

    command = await actor.invoke_mock_command("power on")
    assert command.status.did_fail


async def test_power_off(actor):
    command = await actor.invoke_mock_command("power off")
    assert command.status.did_succeed


async def test_status(actor):
    command = await actor.invoke_mock_command("ieb status")

    assert command.status.did_succeed
    assert actor.mock_replies[-1]["power_gfa"] == "F,F,F,F,F,F"
    assert actor.mock_replies[-1]["power_can"] == "T,T,T,T,T,T"
