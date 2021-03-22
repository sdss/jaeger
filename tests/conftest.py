#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: conftest.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import contextlib
import os

import pytest
from can import Bus, Notifier
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server.async_io import StartTcpServer

import clu.testing
from clu.testing import TestCommand
from sdsstools import read_yaml_file

import jaeger
from jaeger import JaegerActor
from jaeger.testing import VirtualFPS, VirtualPositioner


TEST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "data/virtual_fps.yaml")

# Disable logging to file.
if jaeger.log.fh:
    jaeger.log.removeHandler(jaeger.log.fh)
if jaeger.can_log.fh:
    jaeger.can_log.removeHandler(jaeger.can_log.fh)


@pytest.fixture()
def event_loop(request):
    """A module-scoped event loop."""

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop


@pytest.fixture(scope="session")
def test_config():
    """Yield the test configuration as a dictionary."""

    yield read_yaml_file(TEST_CONFIG_FILE)


@pytest.fixture()
def notifier(test_config, event_loop):
    """Yields a CAN notifier."""

    channel = jaeger.config["profiles"]["virtual"]["channel"]
    notifier = Notifier(Bus(channel, bustype="virtual"), [], loop=event_loop)

    yield notifier

    notifier.stop()


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
async def vfps(event_loop, ieb_server):
    """Sets up the virtual FPS."""

    # Make initialisation faster.
    jaeger.config["fps"]["initialise_timeouts"] = 0.05

    fps = VirtualFPS()
    fps.ieb.client.host = "127.0.0.1"
    fps.ieb.client.port = 5020

    yield fps

    fps.ieb.client.stop()
    fps.can._command_queue_task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await fps.can._command_queue_task


@pytest.fixture()
async def vpositioners(test_config, notifier, event_loop):
    """Yields positioners."""

    vpositioners = []
    for pid in test_config["positioners"]:
        vpositioners.append(
            VirtualPositioner(
                pid,
                notifier=notifier,
                loop=event_loop,
                **test_config["positioners"][pid],
            )
        )

    yield vpositioners

    for vpositioner in vpositioners:
        await vpositioner.shutdown()


@pytest.fixture
async def actor(vfps):

    await vfps.initialise()
    await asyncio.sleep(0.1)

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
