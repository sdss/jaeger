#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_trajectory.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest

from jaeger import config
from jaeger.commands.trajectory import send_trajectory
from jaeger.exceptions import JaegerError


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_send_trajectory(vfps):

    await vfps.initialise()

    await vfps.send_trajectory(
        {
            1: {
                "alpha": [(1, 1), (2, 2)],
                "beta": [(1, 1), (2, 2)],
            }
        },
        use_sync_line=False,
    )

    await vfps.update_position()


async def test_disabled_positioner_fails(vfps):

    await vfps.initialise()
    vfps[1].disabled = True

    with pytest.raises(JaegerError) as err:
        await send_trajectory(
            vfps,
            {
                1: {
                    "alpha": [(1, 1), (2, 2)],
                    "beta": [(1, 1), (2, 2)],
                }
            },
            use_sync_line=False,
        )

    assert "positioner_id=1 is disabled" in str(err)


@pytest.mark.xfail
async def test_validate_out_of_limits(vfps):

    await vfps.initialise()

    with pytest.raises(JaegerError) as err:
        await send_trajectory(
            vfps,
            {
                1: {
                    "alpha": [(1000, 1), (2, 2)],
                    "beta": [(1, 1), (2, 2)],
                }
            },
        )

    assert "out of range" in str(err)


@pytest.mark.parametrize("beta,safe_mode", [(150, True), (160, {"min_beta": 170})])
async def test_validate_safe_mode(vfps, monkeypatch, beta, safe_mode):

    monkeypatch.setitem(config, "safe_mode", safe_mode)

    await vfps.initialise()

    with pytest.raises(JaegerError) as err:
        await send_trajectory(
            vfps,
            {
                1: {
                    "alpha": [(beta, 1), (2, 2)],
                    "beta": [(1, 1), (2, 2)],
                }
            },
        )

    assert "safe mode is on" in str(err)
