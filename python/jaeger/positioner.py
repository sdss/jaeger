#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-08 13:32:53

import asyncio

from jaeger import config, log
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

        self.centre = None
        self.alpha = None
        self.beta = None
        self.status = maskbits.PositionerStatus.UNKNOWN
        self.firmware = None

        if self.position_watcher is not None:
            self.position_watcher.cancel()

    async def _postion_watcher_periodic(self, delay):
        """Updates the position each ``delay`` seconds."""

        while True:
            command = self.fps.send_command(CommandID.GET_ACTUAL_POSITION,
                                            positioner_id=self.positioner_id)
            await command

            try:
                self.alpha, self.beta = command.get_positions()
            except ValueError:
                log.debug(f'positioner {self.positioner_id}: '
                          'failed to receive current position.')

            log.debug(f'(alpha, beta)={self.alpha, self.beta}')

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

        status_int = int(bytes_to_int(command.replies[0].data))

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
            The status to wait for. Can be a list in which case it will wait
            until all the statuses in the list have been reached.
        delay : float
            How many seconds to sleep between polls to get the current status.
        timeout : float
            How many seconds to wait for the status to reach the desired value
            before aborting.

        Returns
        -------
        result : `bool`
            Returns `True` if the status has been reached or `False` if the
            timeout limit was reached.

        """

        if not isinstance(status, (list, tuple)):
            status = [status]

        async def status_poller(wait_for_status):

            while True:
                await self.update_status()

                # Check all statuses in the list
                all_reached = True
                for ss in wait_for_status:
                    if ss not in self.status:
                        all_reached = False

                if all_reached:
                    return

                await asyncio.sleep(delay)

        wait_for_status = [maskbits.PositionerStatus(ss) for ss in status]

        assert not self.is_bootloader(), \
            'this coroutine cannot be scheduled in bootloader mode.'

        try:
            await asyncio.wait_for(status_poller(wait_for_status), timeout)
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

        log.info(f'positioner {self.positioner_id}: initialising datums')

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

        await self._set_speed(alpha=config['motor_speed'],
                              beta=config['motor_speed'])

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split('.')[1] == '80'

    async def _set_speed(self, alpha, beta):
        """Sets motor speeds."""

        speed_command = self.fps.send_command(CommandID.SET_SPEED,
                                              positioner_id=self.positioner_id,
                                              alpha=float(alpha),
                                              beta=float(beta))

        await speed_command

        response_code = speed_command.replies[0].response_code
        if response_code != maskbits.ResponseCode.COMMAND_ACCEPTED:
            log.error(f'positioner {self.positioner_id}: failed setting speed.'
                      f' Command returned code {response_code.name}.')
            return False

        return speed_command

    async def _goto_position(self, alpha, beta, relative=False):
        """Go to a position."""

        command_id = CommandID.GO_TO_RELATIVE_POSITION \
            if relative else CommandID.GO_TO_ABSOLUTE_POSITION

        goto_command = self.fps.send_command(command_id,
                                             positioner_id=self.positioner_id,
                                             alpha=float(alpha),
                                             beta=float(beta))

        await goto_command

        response_code = goto_command.replies[0].response_code
        if response_code != maskbits.ResponseCode.COMMAND_ACCEPTED:
            log.error(f'positioner {self.positioner_id}: '
                      f'failed going to position. '
                      f'Command returned code {response_code.name}.')
            return False

        return goto_command

    async def goto(self, alpha=None, beta=None,
                   alpha_speed=None, beta_speed=None, relative=False):
        """Moves positioner to a given position.

        Parameters
        ----------
        alpha : float
            The position where to move the alpha arm, in degrees.
        beta : float
            The position where to move the beta arm, in degrees.
        alpha_speed : float
            The speed of the alpha arm, in RPM.
        beta_speed : float
            The speed of the beta arm, in RPM.
        relative : bool
            Whether the movement is absolute or relative to the current
            position.

        Returns
        -------
        result : `bool`
            `True` if both arms have reached the desired position, `False` if
            a problem was found.

        Examples
        --------
        ::

            # Move alpha and beta at the currently set speed
            >>> await goto(alpha=100, beta=10)

            # Set the speed of the alpha arm
            >>> await goto(alpha_speed=1000)

        """

        assert alpha or beta or alpha_speed or beta_speed, 'no inputs.'

        # Set the speed
        if alpha_speed is not None or beta_speed is not None:

            assert alpha_speed is not None and beta_speed is not None, \
                'the speed for both arms needs to be provided.'

            log.info(f'positioner {self.positioner_id}: setting speed '
                     f'({float(alpha_speed):.2f}, {float(alpha_speed):.2f})')

            if not await self._set_speed(alpha_speed, beta_speed):
                return False

        # Go to position
        if alpha is not None or beta is not None:

            assert alpha is not None and beta is not None, \
                'the position for both arms needs to be provided.'

            log.info(f'positioner {self.positioner_id}: goto position '
                     f'({float(alpha):.3f}, {float(beta):.3f}) degrees')

            goto_command = await self._goto_position(alpha, beta,
                                                     relative=relative)

            if not goto_command:
                return False

            # Sleeps for the time the firmware believes it's going to take
            # to get to the desired position.
            alpha_time, beta_time = goto_command.get_move_time()

            move_time = max([alpha_time, beta_time])

            log.info(f'the move will take {move_time} seconds')

            await asyncio.sleep(move_time)

            # Blocks until we're sure both arms at at the position.
            result = await self.wait_for_status(
                [maskbits.PositionerStatus.ALPHA_DISPLACEMENT_COMPLETED,
                 maskbits.PositionerStatus.BETA_DISPLACEMENT_COMPLETED],
                timeout=3)

            if result is False:
                log.error(f'positioner {self.positioner_id}: '
                          'failed to reach commanded position.')

            log.info(f'positioner {self.positioner_id}: position reached.')

        return True

    def __repr__(self):
        status_names = '|'.join([status.name for status in self.status.active_bits])
        return f'<Positioner (id={self.positioner_id}, status={status_names!r})>'
