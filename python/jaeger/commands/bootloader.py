#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-09
# @Filename: bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-11 15:59:37

import pathlib

from jaeger import commands
from jaeger.utils.maskbits import BootloaderStatus


__ALL__ = ['load_firmware']


def load_firmware(fps, firmware_file, chunk_check=1000):
    """Convenience function to run through the steps of loading a new firmware.

    Performs a series of initial checks to make sure all the actors are in
    bootloader mode and ready to accept new firmware. Sends the firmware data
    and every ``chunk_check`` bytes sent, confirms that the transmission is
    ongoing. After the payload has been sent, confirms that the new firmware
    is now active.

    Parameters
    ----------
    fps : `~jaeger.fps.FPS`
        `~jaeger.fps.FPS` instance to which the commands will be sent.
    firmware_file : str
        Binary file containing the firmware to load.
    chunk_check : int
        Interval, in bytes, at which status request will be sent to confirm
        the firmware load is happenning correctly.

    Returns
    -------
    status : `.BootloaderStatus`
        The status bit of the bootloader progress.

    """

    firmware_file = pathlib.Path(firmware_file)

    assert firmware_file.exists(), 'cannot find firmware file'

    # Open firmware data as binary.
    firmware_data = open(firmware_file, 'rb')

    # Here we may want to do a check to make sure we are in bootloader mode.
    # Since the status bitmask between normal and bootloader have bits in
    # common, we cannot use a get status. Instead, we could use something like
    # command 20 (initialise datums) that should return an invalid bootloader
    # command status code.

    # First we check what positioners are online.
    get_id_cmd = commands.GetID(positioner_id=0)
    get_id_reply = get_id_cmd.send_command()
