#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: conftest.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

# flake8: noqa E402

import asyncio
import contextlib
import os
import sys
import urllib.request
from unittest.mock import MagicMock

import pytest
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server.async_io import StartTcpServer

import clu.testing
from clu.testing import TestCommand
from sdsstools import read_yaml_file


sys.modules["coordio.transforms"] = MagicMock()


@pytest.fixture(scope="session")
def setup_config():

    import jaeger
    from jaeger import config

    TEST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data/virtual_fps.yaml")

    config["safe_mode"] = False
    config["files"]["ieb_config"] = "etc/ieb_APO.yaml"
    config["fps"]["start_pollers"] = True

    config["fps"]["snapshot_path"] = "/var/tmp/logs/jaeger/snapshots"
    config["fps"]["trajectory_dump_path"] = "/var/tmp/logs/jaeger/trajectories"
    config["fps"]["use_lock"] = False

    if os.environ.get("CI", False):
        tmp = os.environ.get("RUNNER_TEMP")

        for section, subsection in [
            ("actor", "log_dir"),
            ("fps", "snapshot_path"),
            ("fps", "configuration_snapshot_path"),
            ("positioner", "trajectory_dump_path"),
        ]:
            config[section][subsection] = config[section][subsection].replace(
                "/data", tmp
            )

    # Disable logging to file.
    if jaeger.log.fh:
        jaeger.log.removeHandler(jaeger.log.fh)
    if jaeger.can_log.fh:
        jaeger.can_log.removeHandler(jaeger.can_log.fh)

    yield TEST_CONFIG_FILE


# @pytest.fixture(autouse=True)
# def mock_fvctransformlco(monkeypatch, mocker):

#     print("here")
#     monkeypatch.setattr("coordio.transforms.FVCTransformLCO", mocker.MagicMock())


@pytest.fixture(scope="session", autouse=True)
def download_data():
    """Download large files needed for testing."""

    FILES = {"fimg-fvcn-0059.fits": "s/utedos4d6sjlvek/fimg-fvcn-0059.fits?dl=0"}

    for fname in FILES:
        outpath = os.path.join(os.path.dirname(__file__), "data", fname)
        if os.path.exists(outpath):
            continue

        url = os.path.join("https://dl.dropboxusercontent.com", FILES[fname])
        urllib.request.urlretrieve(url, outpath)


@pytest.fixture(scope="session")
def test_config(setup_config):
    """Yield the test configuration as a dictionary."""

    yield read_yaml_file(setup_config)


@pytest.fixture()
async def ieb_server(event_loop):

    store = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 100),
        co=ModbusSequentialDataBlock(512, [0] * 100),
        hr=ModbusSequentialDataBlock(512, [0] * 100),
        ir=ModbusSequentialDataBlock(0, [0] * 100),
    )

    context = ModbusServerContext(slaves=store, single=True)

    server = await StartTcpServer(
        context,
        address=("127.0.0.1", 5020),
        loop=event_loop,
        allow_reuse_address=True,
        allow_reuse_port=True,
    )

    task = event_loop.create_task(server.serve_forever())

    yield server

    server.server_close()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.fixture()
async def vfps(ieb_server, monkeypatch):
    """Sets up the virtual FPS."""

    import jaeger
    from jaeger.can import JaegerCAN
    from jaeger.ieb import IEB
    from jaeger.testing import VirtualFPS

    # Make initialisation faster.
    monkeypatch.setitem(jaeger.config["fps"], "initialise_timeouts", 0.05)

    fps = await VirtualFPS.create(initialise=False)
    fps.pid_lock = True  # type: ignore  # Hack to prevent use of lock.

    assert isinstance(fps.ieb, IEB)
    fps.ieb.client.host = "127.0.0.1"
    fps.ieb.client.port = 5020
    await asyncio.sleep(0.01)  # Give time to the IEB server to serve.

    async with fps:
        yield fps

    assert isinstance(fps.can, JaegerCAN) and fps.can._command_queue_task

    fps.ieb.client.stop()
    fps.can._command_queue_task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await fps.can._command_queue_task


@pytest.fixture()
async def vpositioners(test_config, vfps):
    """Yields positioners."""

    for pid in test_config["positioners"]:
        vfps.add_virtual_positioner(pid)

    await vfps.initialise()  # Reinitialise

    yield vfps._vpositioners


@pytest.fixture
async def actor(vfps):

    from jaeger.actor import JaegerActor

    await vfps.initialise()
    await asyncio.sleep(0.001)

    jaeger_actor = JaegerActor(
        vfps,
        name="test_actor",
        host="localhost",
        port=19990,
        log_dir=False,
    )
    jaeger_actor = await clu.testing.setup_test_actor(jaeger_actor)  # type: ignore

    yield jaeger_actor

    # Clear replies in preparation for next test.
    jaeger_actor.mock_replies.clear()


@pytest.fixture
async def command(actor):

    command = TestCommand(commander_id=1, actor=actor)
    yield command
