#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-07 23:00:42

import asyncio

from jaeger.commands import CommandID
from jaeger.utils import StatusMixIn, bytes_to_int, maskbits


__ALL__ = ['Positioner']


class Positioner(StatusMixIn):
    r"""Represents the status and parameters of a positioner.

    Parameters
    ----------
    positioner_id : int
        The ID of the positioner
    fps : `~jaeger.fps.FPS`
        The `~jaeger.fps.FPS` instance to which this positioner is linked to.
    position : tuple
        The :math:`(x_{\rm focal}, y_{\rm focal})` coordinates of the
        central axis of the positioner.
    alpha : float
        Position of the alpha arm, in degrees.
    beta : float
        Position of the beta arm, in degrees.

    """

    def __init__(self, positioner_id, fps, position=None, alpha=None, beta=None):

        self.fps = fps
        self.positioner_id = positioner_id
        self.position = position
        self.alpha = alpha
        self.beta = beta
        self.firmware = None

        super().__init__(maskbit_flags=maskbits.PositionerStatus,
                         initial_status=maskbits.PositionerStatus.UNKNOWN)

    def reset(self):
        """Resets positioner values and statuses."""

        self.position = None
        self.alpha = None
        self.beta = None
        self.status = maskbits.PositionerStatus.UNKNOWN
        self.firmware = None

    async def get_firmware(self):
        """Updates the firmware version."""

        command = self.fps.send_command(CommandID.GET_FIRMWARE_VERSION,
                                        positioner_id=self.positioner_id)
        await command

        self.firmware = command.get_firmware()

    async def update_status(self):
        """Updates the status of the positioner."""

        command = self.fps.send_command(CommandID.GET_STATUS,
                                        positioner_id=self.positioner_id)
        await command

        status_int = int(bytes_to_int(command.replies[0]))

        if self.is_bootloader():
            self.flag = maskbits.PositionerStatus
        else:
            self.flag = maskbits.BootloaderStatus

        self.status = self.flag(status_int)

    async def wait_for_status(self, status, delay=0.1, timeout=None):
        """Polls the status until it reaches a certain value.

        Parameters
        ----------
        status : `~jaeger.maskbits.PositionerStatus`
            The status to wait for.
        delay : float
            How many seconds to sleep between polls to get the current status.
        timeout : float
            How many seconds to wait for the status to reach the desired value
            before aborting.

        Returns
        -------
        result : bool
            Returns `True` if the status has been reached or `False` if the
            timeout limit was reached.

        """

        async def status_poller(wait_for_status):
            while True:
                await self.update_status()
                if wait_for_status in self.status:
                    break
                await asyncio.sleep(delay)

        wait_for_status = maskbits.PositionerStatus(status)

        assert not self.is_bootloader(), \
            'this coroutine cannot be scheduled in bootloader mode.'

        try:
            await asyncio.wait_for(status_poller(), timeout, wait_for_status)
        except asyncio.TimeoutError:
            return False

        return True

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split('.')[1] == '80'

    def __repr__(self):
        status_names = '|'.join([status.name for status in self.status.active_bits])
        return f'<Positioner (id={self.positioner_id}, status={status_names!r})>'
