#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-09
# @Filename: bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-10 18:44:09

import asyncio
import os
import pathlib
import zlib

from jaeger import commands, log
from jaeger.core.exceptions import JaegerError, JaegerUserWarning
from jaeger.maskbits import BootloaderStatus
from jaeger.utils import int_to_bytes


__ALL__ = ['load_firmware', 'StartFirmwareUpgrade', 'GetFirmwareVersion',
           'SendFirmwareData']


async def load_firmware(fps, firmware_file, positioners=None, force=False):
    """Convenience function to run through the steps of loading a new firmware.

    This function is a coroutine and not intendend for direct use. Use the
    ``jaeger`` CLI instead.

    Parameters
    ----------
    fps : `~jaeger.fps.FPS`
        `~jaeger.fps.FPS` instance to which the commands will be sent.
    firmware_file : str
        Binary file containing the firmware to load.
    positioners : `list` or `None`
        A list of positioner ids whose firmware to update, or `None` to update
        all the positioners in ``fps``.
    force : bool
        Forces the firmware load to continue even if some positioners are not
        responding or are not in bootloader mode.

    """

    firmware_file = pathlib.Path(firmware_file)
    assert firmware_file.exists(), 'cannot find firmware file'

    log.info(f'firmware file {firmware_file!s} found.')

    # Open firmware data as binary.
    firmware_data = open(firmware_file, 'rb')
    crc32 = zlib.crc32(firmware_data.read())
    filesize = os.path.getsize(firmware_file)

    # Check to make sure all positioners are in bootloader mode.
    valid_positioners = []

    for positioner_id in fps.positioners:

        if positioners is not None and positioner_id not in positioners:
            continue

        positioner = fps.positioners[positioner_id]

        if (not positioner.is_bootloader() or
                BootloaderStatus.BOOTLOADER_INIT not in positioner.status or
                BootloaderStatus.UNKNOWN in positioner.status):

            msg = (f'positioner_id={positioner_id} not in bootloader '
                   'mode or state is invalid.')
            if force:
                log.warning(msg + ' Skipping because force=True.',
                            JaegerUserWarning)
                continue

            raise JaegerError(msg)

        valid_positioners.append(positioner)

    start_firmware_payload = int_to_bytes(crc32) + int_to_bytes(filesize)

    log.info(f'CRC32: {start_firmware_payload[0:4]}')
    log.info(f'File size: {start_firmware_payload[4:]}')

    await asyncio.gather(
        *[fps.send_command(commands.CommandID.START_FIRMWARE_UPGRADE,
                           positioner_id=positioner.positioner_id,
                           data=start_firmware_payload)
          for positioner in valid_positioners])

    # Restore seek to start of file
    firmware_data.seek(0)

    while True:

        chunk = firmware_data.read(8)
        packetdata = bytearray(chunk)
        packetdata.reverse()  # IMPORTANT!

        if len(packetdata) == 0:
            break

        await asyncio.gather(
            *[fps.send_command(commands.CommandID.SEND_FIRMWARE_DATA,
                               positioner_id=positioner.positioner_id,
                               data=packetdata)
              for positioner in valid_positioners])

    log.info('firmware upgrade complete.')


class GetFirmwareVersion(commands.Command):

    command_id = commands.CommandID.GET_FIRMWARE_VERSION
    broadcastable = True

    def get_firmware(self, positioner_id=None):
        """Returns the firmware version string.

        Parameters
        ----------
        positioner_id : int
            The positioner for which to return the version. This parameter is
            ignored unless the command is a broadcast. If `None` and the
            command is a broadcast, returns a list with the firmware version of
            all the positioners, in the order of `GetFirmwareVersion.replies`.

        Returns
        -------
        firmware : `str` or `list`
            A string or list of string with the firmware version(s), with the
            format ``'XX.YY.ZZ'`` where ``YY='80'`` if the positioner is in
            bootloader mode.

        Raises
        ------
        ValueError
            If no positioner with ``positioner_id`` has replied.

        """

        def format_version(reply):
            return '.'.join(format(byt, '02d') for byt in reply.data[1:])

        # If not a broadcast, use the positioner_id of the command
        if self.positioner_id != 0:
            positioner_id = self.positioner_id

        if len(self.replies) == 0:
            raise ValueError('no positioners have replied to this command.')

        if positioner_id is None:
            return [format_version(reply) for reply in self.replies]
        else:
            return format_version(self.get_reply_for_positioner(positioner_id))


class StartFirmwareUpgrade(commands.Command):

    command_id = commands.CommandID.START_FIRMWARE_UPGRADE
    broadcastable = False


class SendFirmwareData(commands.Command):

    command_id = commands.CommandID.SEND_FIRMWARE_DATA
    broadcastable = False
