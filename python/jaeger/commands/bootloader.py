#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-09
# @Filename: bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import contextlib
import os
import pathlib
import time
import warnings
import zlib

import numpy

from jaeger import log
from jaeger.commands import Command, CommandID
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.maskbits import BootloaderStatus
from jaeger.utils import int_to_bytes


__all__ = [
    "load_firmware",
    "StartFirmwareUpgrade",
    "GetFirmwareVersion",
    "SendFirmwareData",
]


async def load_firmware(
    fps,
    firmware_file,
    positioners=None,
    force=False,
    show_progressbar=False,
    progress_callback=None,
):
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
    show_progressbar : bool
        Whether to show a progress bar.
    progress_callback : bool
        A function to call as data gets transferred to the positioners. The
        callback is called with ``(current_chunk, n_chuck)`` where
        ``current_chunk`` is the number of the data chunk being sent and
        ``n_chunk`` is the total number of chunks in the data package.

    """

    if show_progressbar:
        try:
            import progressbar
        except ImportError:
            warnings.warn(
                "progressbar2 is not installed. Cannot show a progress bar.",
                JaegerUserWarning,
            )
            progressbar = None
            show_progressbar = False
    else:
        progressbar = None

    start_time = time.time()

    firmware_file = pathlib.Path(firmware_file)
    assert firmware_file.exists(), "cannot find firmware file"

    log.info(f"firmware file {firmware_file!s} found.")

    # Open firmware data as binary.
    firmware_data = open(firmware_file, "rb")
    crc32 = zlib.crc32(firmware_data.read())
    filesize = os.path.getsize(firmware_file)

    # Check to make sure all positioners are in bootloader mode.
    valid_positioners = []
    n_bad = 0

    for positioner_id in fps.positioners:

        if positioners is not None and positioner_id not in positioners:
            continue

        positioner = fps.positioners[positioner_id]

        if (
            not positioner.is_bootloader()
            or BootloaderStatus.BOOTLOADER_INIT not in positioner.status
            or BootloaderStatus.UNKNOWN in positioner.status
        ):

            n_bad += 1
            continue

        valid_positioners.append(positioner)

    if len(valid_positioners) == 0:
        raise JaegerError(
            "no positioners found in bootloader mode or with valid status."
        )
        return

    if n_bad > 0:

        msg = f"{n_bad} positioners not in bootloader mode or state is invalid."
        if force:
            warnings.warn(msg + " Proceeding becasuse force=True.", JaegerUserWarning)
        else:
            raise JaegerError(msg)

    log.info("stopping pollers")
    await fps.pollers.stop()

    log.info(f"upgrading firmware on {len(valid_positioners)} positioners.")

    start_firmware_payload = int_to_bytes(filesize) + int_to_bytes(crc32)

    log.info(f"CRC32: {crc32}")
    log.info(f"File size: {filesize} bytes")

    cmds = [
        fps.send_command(
            CommandID.START_FIRMWARE_UPGRADE,
            positioner_id=positioner.positioner_id,
            data=start_firmware_payload,
        )
        for positioner in valid_positioners
    ]

    await asyncio.gather(*cmds)

    if any(cmd.status.failed or cmd.status.timed_out for cmd in cmds):
        log.error("firmware upgrade failed.")
        return False

    # Restore pointer to start of file
    firmware_data.seek(0)

    log.info("starting data send.")

    chunk_size = 8
    n_chunks = int(numpy.ceil(filesize / chunk_size))

    with contextlib.ExitStack() as stack:

        if show_progressbar:
            bar = stack.enter_context(progressbar.ProgressBar(max_value=n_chunks))
        else:
            bar = None

        ii = 0
        while True:

            chunk = firmware_data.read(chunk_size)
            packetdata = bytearray(chunk)
            # packetdata.reverse()  # IMPORTANT! no longer needed for P1

            if len(packetdata) == 0:
                break

            cmds = [
                fps.send_command(
                    CommandID.SEND_FIRMWARE_DATA,
                    positioner_id=positioner.positioner_id,
                    data=packetdata,
                    timeout=15,
                )
                for positioner in valid_positioners
            ]

            await asyncio.gather(*cmds)

            if any(cmd.status.failed or cmd.status.timed_out for cmd in cmds):
                log.error("firmware upgrade failed.")
                return False

            ii += 1
            if show_progressbar:
                bar.update(ii)

            if progress_callback:
                progress_callback(ii, n_chunks)

    log.info("firmware upgrade complete.")

    total_time = time.time() - start_time
    log.info(f"upgrading firmware took {total_time:.2f}")

    return True


class GetFirmwareVersion(Command):

    command_id = CommandID.GET_FIRMWARE_VERSION
    broadcastable = True
    safe = True
    bootloader = True

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
            return ".".join(format(byt, "02d") for byt in reply.data[0:3][::-1])

        # If not a broadcast, use the positioner_id of the command
        if self.positioner_id != 0:
            positioner_id = self.positioner_id

        if len(self.replies) == 0:
            raise ValueError("no positioners have replied to this command.")

        if positioner_id is None:
            return [format_version(reply) for reply in self.replies]
        else:
            reply = self.get_reply_for_positioner(positioner_id)
            if reply:
                return format_version(reply)
            else:
                return None

    @staticmethod
    def encode(firmware):
        """Returns the bytearray encoding the firmware version."""

        chunks = firmware.split(".")[::-1]

        data = b""
        for chunk in chunks:
            data += int_to_bytes(int(chunk), "u1")

        return data


class StartFirmwareUpgrade(Command):

    command_id = CommandID.START_FIRMWARE_UPGRADE
    broadcastable = False
    safe = True
    bootloader = True


class SendFirmwareData(Command):

    command_id = CommandID.SEND_FIRMWARE_DATA
    broadcastable = False
    safe = True
    bootloader = True
