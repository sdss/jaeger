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
    command = await actor.invoke_mock_command("ieb power on")
    assert command.status.did_succeed


async def test_power_on_fails(actor, mocker):

    # Open PS1 and make it do nothing when it closes.
    ps1 = actor.fps.ieb.get_device("DO.PS1")
    await ps1.open()
    mocker.patch.object(ps1, "write")

    command = await actor.invoke_mock_command("ieb power on")
    assert command.status.did_fail


async def test_power_off(actor):
    command = await actor.invoke_mock_command("ieb power off")
    assert command.status.did_succeed
