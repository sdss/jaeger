#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-24
# @Filename: test_bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import pathlib

import pytest

from jaeger import maskbits
from jaeger.commands.bootloader import load_firmware


pytestmark = [pytest.mark.usefixtures('positioners'), pytest.mark.asyncio]


async def test_bootloader(vfps, positioners):

    await vfps.initialise()

    for vpositioner in positioners:
        vpositioner.set_bootloader()

    await vfps.update_firmware_version()

    for positioner in vfps.values():
        assert positioner.is_bootloader()
        assert maskbits.BootloaderStatus.BOOTLOADER_INIT in positioner.status


async def test_load_firmware(vfps, positioners):

    for positioner in positioners:
        positioner.set_bootloader()

    await vfps.initialise()

    firmware_file = pathlib.Path(__file__).parent / 'data/firmware.bin'
    firmware_version = open(firmware_file).read().strip()[-8:]

    await load_firmware(vfps, firmware_file, positioners=[1], force=True)

    for positioner in positioners:
        positioner.set_bootloader(False)

    await vfps[1].update_status()

    assert vfps[1].firmware == firmware_version
