#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-09
# @Filename: bootloader.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
import time
import warnings
import zlib

from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import numpy

from jaeger import can_log, config, log
from jaeger.commands import Command, CommandID
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.maskbits import BootloaderStatus
from jaeger.utils import int_to_bytes


if TYPE_CHECKING:
    from jaeger import FPS


__all__ = [
    "load_firmware",
    "StartFirmwareUpgrade",
    "GetFirmwareVersion",
    "SendFirmwareData",
]


async def load_firmware(
    fps: FPS,
    firmware_file: str | pathlib.Path,
    positioners: Optional[List[int]] = None,
    messages_per_positioner: Optional[int] = None,
    force: bool = False,
    show_progressbar: bool = False,
    progress_callback: Optional[Callable[[int, int], Any]] = None,
    stop_logging: bool = True,
):
    """Convenience function to run through the steps of loading a new firmware.

    This function is a coroutine and not intendend for direct use. Use the
    ``jaeger`` CLI instead.

    Parameters
    ----------
    fps
        `~jaeger.fps.FPS` instance to which the commands will be sent.
    firmware_file
        Binary file containing the firmware to load.
    positioners
        A list of positioner ids whose firmware to update, or `None` to update
        all the positioners in ``fps``.
    messages_per_positioner
        How many messages to send to each positioner at once. This can improve the
        performance but also overflow the CAN bus buffer. With the default value of
        `None`, reverts to the configuration value
        ``positioner.firmware_messages_per_positioner``.
    force
        Forces the firmware load to continue even if some positioners are not
        responding or are not in bootloader mode.
    show_progressbar
        Whether to show a progress bar.
    progress_callback
        A function to call as data gets transferred to the positioners. The
        callback is called with ``(current_chunk, n_chuck)`` where
        ``current_chunk`` is the number of the data chunk being sent and
        ``n_chunk`` is the total number of chunks in the data package.
    stop_logging
        Disable logging to file for the CAN logger to improve performance.

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

    pids = [pos.positioner_id for pos in valid_positioners]

    cmd = await fps.send_command(
        CommandID.START_FIRMWARE_UPGRADE,
        positioner_ids=pids,
        data=[start_firmware_payload],
    )

    if cmd.status.failed or cmd.status.timed_out:
        log.error("firmware upgrade failed.")
        return False

    # Restore pointer to start of file
    firmware_data.seek(0)

    log.info("starting data send.")

    if stop_logging and can_log.fh:
        fh_handler = can_log.handlers.pop(can_log.handlers.index(can_log.fh))
    else:
        fh_handler = None

    chunk_size = 8
    n_chunks = int(numpy.ceil(filesize / chunk_size))

    with contextlib.ExitStack() as stack:

        if show_progressbar and progressbar:
            bar = stack.enter_context(progressbar.ProgressBar(max_value=n_chunks))
        else:
            bar = None

        messages_default = config["positioner"]["firmware_messages_per_positioner"]
        messages_per_positioner = messages_per_positioner or messages_default
        assert isinstance(messages_per_positioner, int)

        ii = 0
        while True:
            cmds = []
            stop = False
            for __ in range(messages_per_positioner):
                chunk = firmware_data.read(chunk_size)
                packetdata = bytearray(chunk)
                # packetdata.reverse()  # IMPORTANT! no longer needed for P1

                if len(packetdata) == 0:
                    stop = True
                    break

                cmds.append(
                    fps.send_command(
                        CommandID.SEND_FIRMWARE_DATA,
                        positioner_ids=pids,
                        data=[packetdata],
                        timeout=15,
                    )
                )

            await asyncio.gather(*cmds)

            if any(cmd.status.failed or cmd.status.timed_out for cmd in cmds):
                log.error("firmware upgrade failed.")
                if fh_handler:
                    can_log.addHandler(fh_handler)
                return False

            ii += messages_per_positioner

            if show_progressbar and bar:
                if ii < n_chunks:
                    bar.update(ii)

            if progress_callback:
                progress_callback(ii, n_chunks)

            if stop:
                break

    log.info("firmware upgrade complete.")

    if fh_handler:
        can_log.addHandler(fh_handler)

    total_time = time.time() - start_time
    log.info(f"upgrading firmware took {total_time:.2f} s.")

    return True


class GetFirmwareVersion(Command):

    command_id = CommandID.GET_FIRMWARE_VERSION
    broadcastable = True
    safe = True
    bootloader = True

    def get_replies(self) -> Dict[int, Any]:
        return self.get_firmware()

    def get_firmware(self, positioner_id=None) -> Dict[int, str]:
        """Returns the firmware version string.

        Parameters
        ----------
        positioner_id : int
            The positioner for which to return the version. If `None` returns
            a dictionary with the firmware version of all the positioners that
            replied.

        Returns
        -------
        firmware
            A string or dictionary of string with the firmware version(s),
            with the format ``'XX.YY.ZZ'`` where ``YY='80'`` if the positioner
            is in bootloader mode.

        Raises
        ------
        ValueError
            If no positioner with ``positioner_id`` has replied.

        """

        def format_version(reply):
            return ".".join(format(byt, "02d") for byt in reply.data[0:3][::-1])

        firmwares = {}
        for reply in self.replies:
            version = format_version(reply)
            firmwares[reply.positioner_id] = version

        return firmwares

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
