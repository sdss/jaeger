#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-23
# @Filename: test_fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import pathlib

import pytest

from drift import Relay

import jaeger
from jaeger.can import JaegerCAN
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.fps import FPS
from jaeger.maskbits import PositionerStatus
from jaeger.testing import VirtualFPS


# Need to mark all tests with positioners to make sure they are created,
# and with asyncio to allow execution of coroutines.
pytestmark = [pytest.mark.usefixtures("vpositioners"), pytest.mark.asyncio]


async def test_vfps(vfps):

    assert isinstance(vfps, VirtualFPS)


async def test_get_id(vfps, vpositioners):

    command = await vfps.send_command(
        "GET_ID",
        positioner_ids=0,
        n_positioners=len(vpositioners),
    )
    assert len(command.replies) == len(vpositioners)


async def test_initialise(vfps, vpositioners):

    await vfps.initialise()

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


async def test_ieb(vfps):

    sync = vfps.ieb.get_device("DO1.SYNC")
    assert isinstance(sync, Relay)

    assert (await sync.read())[0] == "open"


@pytest.mark.xfail()
async def test_positioner_disabled_send_command_fails_broadcast(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    with pytest.raises(JaegerError) as err:
        await vfps.send_command("START_TRAJECTORY", positioner_ids=0)

    assert "Some positioners are disabled." in str(err)


async def test_positioner_disabled_send_command_fails(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    with pytest.raises(JaegerError) as err:
        await vfps.send_command("GO_TO_ABSOLUTE_POSITION", positioner_ids=2)

    assert "Some commanded positioners are disabled." in str(err)


async def test_positioner_disabled_send_to_all(vfps):

    await vfps.initialise()
    vfps[2].disabled = True

    cmd = await vfps.send_command("GET_ID", positioner_ids=None)
    assert len(cmd.replies) == len(vfps) - 1


async def test_vfps_add_positioner(vfps: VirtualFPS):

    vpos = jaeger.Positioner(100)
    vfps.add_positioner(vpos)

    assert len(vfps) == 6


async def test_vfps_add_positioner_exists(vfps: VirtualFPS):

    vpos = jaeger.Positioner(1)
    with pytest.raises(JaegerError):
        vfps.add_positioner(vpos)


async def test_fps_bad_ieb_file():

    vbus = JaegerCAN("virtual", ["virtual"])

    with pytest.warns(JaegerUserWarning):
        fps = FPS(vbus, ieb=pathlib.Path("/some/bad/file.yaml"))
    assert fps.ieb is None


async def test_fps_ieb_none():

    vbus = JaegerCAN("virtual", ["virtual"])

    fps = FPS(vbus, ieb=False)
    assert fps.ieb is None


async def test_fps_ieb_bad_type():

    vbus = JaegerCAN("virtual", ["virtual"])

    with pytest.raises(ValueError):
        FPS(vbus, ieb=[1, 2, 3])  # type: ignore


async def test_fps_add_positioner():

    vbus = JaegerCAN("virtual", ["virtual"])
    fps = await FPS.create(vbus)

    fps.add_positioner(5, interface=0, bus=1)  # Add positioner zero to first interface
    assert fps.positioner_to_bus[5] == (vbus.interfaces[0], 1)


@pytest.mark.xfail()
async def test_disable_collision(vfps, vpositioners, monkeypatch):

    monkeypatch.setitem(
        jaeger.config["fps"],
        "disable_collision_detection_positioners",
        [2],
    )

    with pytest.warns(JaegerUserWarning) as w:
        await vfps.initialise()
        msg = str(w[-1].message)
        assert msg == "Disabling collision detection for positioners [2]."


@pytest.mark.xfail()
async def test_lock_unlock(vfps, vpositioners):

    assert vfps.locked is False

    await vfps.lock(by=[1])

    assert vfps.locked is True
    assert vfps.locked_by == [1]

    await vfps.unlock()
    assert vfps.locked is False
    assert vfps.locked_by == []


@pytest.mark.xfail()
async def test_unlock_fails(vfps, vpositioners, mocker):

    mocker.patch.object(vfps, "update_status")

    await vfps.lock()
    vfps[1].status = PositionerStatus.COLLISION_BETA

    with pytest.raises(JaegerError):
        await vfps.unlock()


@pytest.mark.xfail()
@pytest.mark.parametrize("positioner_ids", [1, [1, 2, 3], None])
async def test_goto(vfps, vpositioners, positioner_ids):

    await vfps.goto(positioner_ids, 10, 10, force=True)


async def test_goto_fails(vfps, vpositioners, mocker):

    mocker.patch("jaeger.fps.goto", side_effect=JaegerError)
    with pytest.raises(JaegerError):
        await vfps.goto({1: (10, 10)})


async def test_report_status(vfps, vpositioners):

    assert isinstance(await vfps.report_status(), dict)


async def test_reinitialise_disabled(vfps, vpositioners):

    vfps[2].disabled = True
    await vfps.initialise(keep_disabled=True)

    assert vfps[2].disabled
