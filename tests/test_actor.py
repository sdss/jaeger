#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2020-07-19
# @Filename: test_actor.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pytest


pytestmark = [pytest.mark.usefixtures('vpositioners'), pytest.mark.asyncio]


async def test_status(actor):

    command = await actor.invoke_mock_command('status')

    assert command.status.did_succeed

    # command running + engineering mode + locked + 5 positioners + done
    assert len(actor.mock_replies) == 9
