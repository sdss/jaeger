#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-09 14:45:50

import asyncio

from jaeger import config, log
from jaeger.commands import CommandID
from jaeger.core.exceptions import JaegerUserWarning
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

    """

    def __init__(self, positioner_id, fps, centre,):

        self.fps = fps
        self.positioner_id = positioner_id
        self.centre = centre
        self.alpha = None
        self.beta = None
        self.firmware = None

        #: A `~asyncio.Task` that polls the current position of alpha and
        #: beta periodically.
        self.position_watcher = None

        #: A `~asyncio.Task` that polls the current status periodically.
        self.status_watcher = None

        # How frequently to poll for status and position.
        # Can be changed in runtime.
        self._position_watcher_delay = 10
        self._status_watcher_delay = 5

        super().__init__(maskbit_flags=maskbits.PositionerStatus,
                         initial_status=maskbits.PositionerStatus.UNKNOWN)

    def reset(self):
        """Resets positioner values and statuses."""

        self.alpha = None
        self.beta = None
        self.status = maskbits.PositionerStatus.UNKNOWN
        self.firmware = None

        self._position_watcher_delay = 10
        self._status_watcher_delay = 5

        if self.position_watcher is not None:
            if not self.position_watcher.cancelled():
                self.position_watcher.cancel()
            self.position_watcher = None

        if self.status_watcher is not None:
            if not self.status_watcher.cancelled():
                self.status_watcher.cancel()
            self.status_watcher = None

    async def _position_watcher_periodic(self):
        """Updates the position each ``self._position_watcher_delay``."""

        while True:
            command = self.fps.send_command(CommandID.GET_ACTUAL_POSITION,
                                            positioner_id=self.positioner_id,
                                            timeout=1)
            await command

            try:
                self.alpha, self.beta = command.get_positions()
            except ValueError:
                log.debug(f'positioner {self.positioner_id}: '
                          'failed to receive current position.')

            log.debug(f'(alpha, beta)={self.alpha, self.beta}')

            await asyncio.sleep(self._position_watcher_delay,
                                loop=self.fps.loop)

    async def _status_watcher_periodic(self):
        """Updates the status each ``self._status_watcher_delay``."""

        while True:
            await self.update_status(timeout=1)
            await asyncio.sleep(self._status_watcher_delay, loop=self.fps.loop)

    async def get_firmware(self):
        """Updates the firmware version."""

        command = self.fps.send_command(CommandID.GET_FIRMWARE_VERSION,
                                        positioner_id=self.positioner_id)
        await command

        self.firmware = command.get_firmware()

    async def update_status(self, timeout=None):
        """Updates the status of the positioner."""

        command = self.fps.send_command(CommandID.GET_STATUS,
                                        positioner_id=self.positioner_id,
                                        timeout=timeout)
        if await command is False:
            log.error(f'{CommandID.GET_STATUS.name!r} failed to complete.')
            return

        if self.is_bootloader():
            self.flag = maskbits.PositionerStatus
        else:
            self.flag = maskbits.BootloaderStatus

        if len(command.replies) == 1:
            status_int = int(bytes_to_int(command.replies[0].data))
            self.status = self.flag(status_int)
        else:
            self.status = self.flag.UNKNOWN

    async def wait_for_status(self, status, delay=0.1, timeout=None):
        """Polls the status until it reaches a certain value.

        Parameters
        ----------
        status : `~jaeger.maskbits.PositionerStatus`
            The status to wait for. Can be a list in which case it will wait
            until all the statuses in the list have been reached.
        delay : float
            How many seconds to sleep between polls to get the current status.
            The original status polling delay is restored at the end of the
            command.
        timeout : float
            How many seconds to wait for the status to reach the desired value
            before aborting.

        Returns
        -------
        result : `bool`
            Returns `True` if the status has been reached or `False` if the
            timeout limit was reached.

        """

        orig_status_delay = self._status_watcher_delay
        self._status_watcher_delay = delay

        if not isinstance(status, (list, tuple)):
            status = [status]

        async def status_poller(wait_for_status):

            while True:
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
            self._status_watcher_delay = orig_status_delay
            return False

        self._status_watcher_delay = orig_status_delay
        return True

    async def initialise(self):
        """Initialises the datum and starts the position watcher."""

        log.info(f'positioner {self.positioner_id}: initialising datums')

        assert not self.is_bootloader(), \
            'this coroutine cannot be scheduled in bootloader mode.'

        PosStatus = maskbits.PositionerStatus

        # Resets all.
        self.reset()

        # Update status
        await self.update_status(timeout=1)

        if PosStatus.DATUM_INITIALIZED not in self.status:

            await self.fps.send_command(CommandID.INITIALIZE_DATUMS,
                                        positioner_id=self.positioner_id)

            result = await self.wait_for_status(PosStatus.DATUM_INITIALIZED,
                                                timeout=2)

            if result is False:
                log.error(f'positioner={self.positioner_id}: '
                          'failed to initialise datums.')
                return False

        if PosStatus.DISPLACEMENT_COMPLETED not in self.status:

            log.warning(f'positioner {self.positioner_id} is moving. '
                        'Stopping trajectories.', JaegerUserWarning)

            await self.fps.send_command(CommandID.STOP_TRAJECTORY,
                                        positioner_id=self.positioner_id)

            result = await self.wait_for_status(
                PosStatus.DISPLACEMENT_COMPLETED, timeout=2)

            if result is False:
                log.error(f'positioner={self.positioner_id}: '
                          'failed to stop trajectory.')
                return False

        # Initialise position poller
        self.position_watcher = self.fps.loop.create_task(
            self._position_watcher_periodic())

        # Initialise status poller
        self.status_watcher = self.fps.loop.create_task(
            self._status_watcher_periodic())

        # Sets the default speed
        if not await self._set_speed(alpha=config['motor_speed'],
                                     beta=config['motor_speed']):
            return False

        return True

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split('.')[1] == '80'

    async def _set_position(self, alpha, beta):
        """Sets the internal position of the motors."""

        set_position_command = self.fps.send_command(
            CommandID.SET_ACTUAL_POSITION,
            positioner_id=self.positioner_id,
            alpha=float(alpha),
            beta=float(beta))

        await set_position_command

        if set_position_command.status.failed:
            return False

        return set_position_command

    async def _set_speed(self, alpha, beta):
        """Sets motor speeds."""

        speed_command = self.fps.send_command(CommandID.SET_SPEED,
                                              positioner_id=self.positioner_id,
                                              alpha=float(alpha),
                                              beta=float(beta))

        await speed_command

        if speed_command.status.failed:
            return False

        return speed_command

    async def _goto_position(self, alpha, beta, relative=False):
        """Go to a position."""

        PositionerStatus = maskbits.PositionerStatus

        if (PositionerStatus.SYSTEM_INITIALIZATION not in self.status or
                PositionerStatus.DATUM_INITIALIZED not in self.status):
            log.error('positioner has not been initialised.')
            return False

        command_id = CommandID.GO_TO_RELATIVE_POSITION \
            if relative else CommandID.GO_TO_ABSOLUTE_POSITION

        goto_command = self.fps.send_command(command_id,
                                             positioner_id=self.positioner_id,
                                             alpha=float(alpha),
                                             beta=float(beta))

        await goto_command

        if goto_command.status.failed:
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

        assert any([var is not None
                    for var in [alpha, beta, alpha_speed, beta_speed]]), \
            'no inputs.'

        # Set the speed
        if alpha_speed is not None or beta_speed is not None:

            assert alpha_speed is not None and beta_speed is not None, \
                'the speed for both arms needs to be provided.'

            log.info(f'positioner {self.positioner_id}: setting speed '
                     f'({float(alpha_speed):.2f}, {float(beta_speed):.2f})')

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

            log.info(f'the move will take {move_time:.2f} seconds')

            # Faster output of positions
            """TODO: because the watcher is sleeping this won't take effect
            until the next loop, so it may sometimes take a long
            time to take effect. We may want to convert that sleep to a Future
            stored in self and be able to cancel it to quickly restart the
            loop.
            """
            orig_position_delay = self._position_watcher_delay
            self._position_watcher_delay = 0.5

            await asyncio.sleep(move_time)

            # Blocks until we're sure both arms at at the position.
            result = await self.wait_for_status(
                maskbits.PositionerStatus.DISPLACEMENT_COMPLETED, timeout=3)

            if result is False:
                log.error(f'positioner {self.positioner_id}: '
                          'failed to reach commanded position.')

            log.info(f'positioner {self.positioner_id}: position reached.')

            # Restore position delay
            self._position_watcher_delay = orig_position_delay

        return True

    def __repr__(self):
        status_names = '|'.join([status.name for status in self.status.active_bits])
        return f'<Positioner (id={self.positioner_id}, status={status_names!r})>'
