#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-07 23:54:55

import asyncio

from jaeger import log
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
    centre : tuple
        The :math:`(x_{\rm focal}, y_{\rm focal})` coordinates of the
        central axis of the positioner.
    alpha : float
        Position of the alpha arm, in degrees.
    beta : float
        Position of the beta arm, in degrees.

    """

    def __init__(self, positioner_id, fps, centre=None, alpha=None, beta=None):

        self.fps = fps
        self.positioner_id = positioner_id
        self.centre = centre
        self.alpha = alpha
        self.beta = beta
        self.firmware = None

        #: A `~asyncio.Task` that polls the current position of alpha and
        #: beta periodically.
        self.position_watcher = None

        super().__init__(maskbit_flags=maskbits.PositionerStatus,
                         initial_status=maskbits.PositionerStatus.UNKNOWN)

    def reset(self):
        """Resets positioner values and statuses."""

        self.position = None
        self.alpha = None
        self.beta = None
        self.status = maskbits.PositionerStatus.UNKNOWN
        self.firmware = None

        if self.position_watcher is not None:
            self.position_watcher.cancel()

    async def _postion_watcher_periodic(self, delay):
        """Updates the position each ``delay`` seconds."""

        while True:
            command = self.fps.send_command(CommandID.GET_ACTUAL_POSTION,
                                            positioner_id=self.positioner_id)
            await command

            try:
                alpha, beta = command.get_position()
            except ValueError:
                log.debug(f'positioner {self.positioner_id}: '
                          'failed to receive current position.')

            await asyncio.sleep(delay, loop=self.fps.loop)

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

    async def initialise(self, delay=1.):
        """Initialises the datum and starts the position watcher.

        Parameters
        ----------
        delay : float
            How frequently to poll for the current position.

        """

        assert not self.is_bootloader(), \
            'this coroutine cannot be scheduled in bootloader mode.'

        if maskbits.PositionerStatus.DATUM_INITIALIZED not in self.status:

            init_datum = self.fps.send_command(CommandID.INITIALIZE_DATUMS,
                                               positioner_id=self.positioner_id)

            await init_datum
            await self.wait_for_status(maskbits.PositionerStatus.DATUM_INITIALIZED)

        # If the watcher is already running, return.
        if self.position_watcher is not None:
            if not self.position_watcher.done() and not self.position_watcher.cancelled():
                return

        self.position_watcher = self.fps.loop.create_task(
            self._postion_watcher_periodic(delay))

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split('.')[1] == '80'

    def __repr__(self):
        status_names = '|'.join([status.name for status in self.status.active_bits])
        return f'<Positioner (id={self.positioner_id}, status={status_names!r})>'
