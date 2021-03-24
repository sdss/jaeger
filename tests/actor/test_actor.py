#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2020-07-19
# @Filename: test_actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import sys

import pytest

from jaeger.maskbits import LowTemperature


pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


@pytest.fixture()
def mock_rtd2(request, mocker, vfps):

    temperature = request.node.get_closest_marker("rtd2").args[0]

    rtd2 = vfps.ieb.get_device("RTD2")

    yield mocker.patch.object(rtd2, "read", return_value=(temperature, "degC"))


async def test_status(actor):

    command = await actor.invoke_mock_command("status")

    assert command.status.did_succeed

    # command running + engineering mode + locked + 5 positioners + done
    assert len(actor.mock_replies) == 10


async def test_info(actor):

    command = await actor.invoke_mock_command("info")
    assert command.status.did_succeed

    data = actor.mock_replies[1:3]
    assert "version" in data[0]
    assert "config_file" in data[1]


@pytest.mark.skipif(sys.version_info < (3, 8), reason="Test fails in PY37")
@pytest.mark.rtd2(-5)
async def test_low_temperature_cold(mock_rtd2, actor):

    await asyncio.sleep(0.1)  # Wait for the first handle_temperature to complete
    assert actor.low_temperature.value == LowTemperature.COLD.value


@pytest.mark.skipif(sys.version_info < (3, 8), reason="Test fails in PY37")
@pytest.mark.rtd2(-15)
async def test_low_temperature_very_cold(mock_rtd2, actor):

    await asyncio.sleep(0.1)
    assert actor.low_temperature.value == LowTemperature.VERY_COLD.value
