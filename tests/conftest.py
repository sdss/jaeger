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
from ruamel.yaml import YAML

import jaeger
from jaeger.testing import VirtualFPS, VirtualPositioner


TEST_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'data/virtual_fps.yaml')

# Disable logging to file.
jaeger.log.removeHandler(jaeger.log.fh)
jaeger.can_log.removeHandler(jaeger.can_log.fh)


@pytest.fixture(scope='module')
def event_loop(request):
    """A module-scoped event loop."""

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop


@pytest.fixture(scope='session')
def test_config():
    """Yield the test configuration as a dictionary."""

    yaml = YAML()
    yield yaml.load(open(TEST_CONFIG_FILE))


@pytest.fixture(scope='module')
def notifier(test_config, event_loop):
    """Yields a CAN notifier."""

    channel = jaeger.config['profiles']['virtual']['channel']
    notifier = Notifier(Bus(channel, bustype='virtual'), [], loop=event_loop)

    yield notifier

    notifier.stop()


@pytest.fixture(scope='function')
async def vfps(event_loop, tmp_path):
    """Sets up the virtual FPS."""

    qa = tmp_path / 'qa.sql'
    fps = VirtualFPS(qa=qa)

    yield fps

    fps.can._command_queue_task.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await fps.can._command_queue_task


@pytest.fixture(scope='module')
async def setup_positioners(test_config, notifier, event_loop):
    """Yields a list of virtual positioners from the configuration files."""

    positioners = []
    for pid in test_config['positioners']:
        positioners.append(VirtualPositioner(pid, notifier=notifier, loop=event_loop,
                                             **test_config['positioners'][pid]))

    yield positioners

    for positioner in positioners:
        await positioner.shutdown()


@pytest.fixture(scope='function')
async def positioners(setup_positioners):
    """Yields positioners."""

    yield setup_positioners

    for positioner in setup_positioners:
        positioner.reset()
