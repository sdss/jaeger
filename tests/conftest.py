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

import clu.testing
from clu.testing import TestCommand
from sdsstools import read_yaml_file

import jaeger
from jaeger import JaegerActor
from jaeger.testing import VirtualFPS, VirtualPositioner


TEST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'data/virtual_fps.yaml')

# Disable logging to file.
jaeger.log.removeHandler(jaeger.log.fh)
jaeger.can_log.removeHandler(jaeger.can_log.fh)


@pytest.fixture()
def event_loop(request):
    """A module-scoped event loop."""

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop


@pytest.fixture(scope='session')
def test_config():
    """Yield the test configuration as a dictionary."""

    yield read_yaml_file(TEST_CONFIG_FILE)


@pytest.fixture()
def notifier(test_config, event_loop):
    """Yields a CAN notifier."""

    channel = jaeger.config['profiles']['virtual']['channel']
    notifier = Notifier(Bus(channel, bustype='virtual'), [], loop=event_loop)

    yield notifier

    notifier.stop()


@pytest.fixture()
async def vfps(event_loop):
    """Sets up the virtual FPS."""

    # Make initialisation faster.
    jaeger.config['fps']['initialise_timeouts'] = 0.05

    fps = VirtualFPS()

    yield fps

    fps.can._command_queue_task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await fps.can._command_queue_task


@pytest.fixture()
async def vpositioners(test_config, notifier, event_loop):
    """Yields positioners."""

    vpositioners = []
    for pid in test_config['positioners']:
        vpositioners.append(VirtualPositioner(pid, notifier=notifier, loop=event_loop,
                                              **test_config['positioners'][pid]))

    yield vpositioners

    for vpositioner in vpositioners:
        await vpositioner.shutdown()


@pytest.fixture
async def actor(vfps):

    await vfps.initialise()
    await asyncio.sleep(0.1)

    jaeger_actor = JaegerActor(vfps, name='test_actor',
                               host='localhost', port=19990,
                               log_dir=False)
    jaeger_actor = await clu.testing.setup_test_actor(jaeger_actor)

    yield jaeger_actor

    # Clear replies in preparation for next test.
    jaeger_actor.mock_replies.clear()


@pytest.fixture
async def command(actor):

    command = TestCommand(commander_id=1, actor=actor)
    yield command
