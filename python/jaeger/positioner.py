#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-10-07
# @Filename: positioner.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import datetime
import warnings
from distutils.version import StrictVersion

from jaeger import config, log, maskbits
from jaeger.commands import CommandID
from jaeger.core.exceptions import JaegerUserWarning
from jaeger.utils import StatusMixIn, bytes_to_int


__ALL__ = ['Positioner', 'VirtualPositioner']


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

    def __init__(self, positioner_id, fps, centre=(None, None)):

        self.fps = fps

        self.positioner_id = positioner_id

        self.centre = centre

        self.alpha = None
        self.beta = None
        self.speed = [None, None]
        self.firmware = None

        self._move_time = None

        super().__init__(maskbit_flags=maskbits.PositionerStatusV4_1,
                         initial_status=maskbits.PositionerStatusV4_1.UNKNOWN)

    @property
    def position(self):
        """Returns a tuple with the ``(alpha, beta)`` position."""

        return (self.alpha, self.beta)

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        if not self.status:
            return False

        return self.status.collision

    @property
    def moving(self):
        """Returns `True` if the positioner is moving."""

        if self.status.DISPLACEMENT_COMPLETED not in self.status:
            return True

        return False

    @property
    def move_time(self):
        """Returns the move time."""

        if not self.moving:
            self._move_time = None

        return self._move_time

    @move_time.setter
    def move_time(self, value):
        """Sets the move time."""

        self._move_time = value

    @property
    def initialised(self):
        """Returns ``True`` if the system and datums have been initialised."""

        if self.status is None:
            return False

        if self.is_bootloader():
            if self.status != maskbits.BootloaderStatus.UNKNOWN:
                return True
            return False

        if (not self.status.initialised or
                self.flags.DATUM_ALPHA_INITIALIZED not in self.status or
                self.flags.DATUM_BETA_INITIALIZED not in self.status):
            return False

        return True

    async def reset(self):
        """Resets positioner values and statuses."""

        self.alpha = None
        self.beta = None
        self.status = self.flags.UNKNOWN
        self.firmware = None

    async def update_position(self, position=None, timeout=1):
        """Updates the position of the alpha and beta arms."""

        if position is None:

            command = self.fps.send_command(CommandID.GET_ACTUAL_POSITION,
                                            positioner_id=self.positioner_id,
                                            timeout=timeout,
                                            silent_on_conflict=True,
                                            override=True)

            await command

            if command.status.failed:
                log.error(f'positioner {self.positioner_id}: failed updating position')
                self.alpha = self.beta = None
                return

            try:
                position = command.get_positions()
            except ValueError:
                log.debug(f'positioner {self.positioner_id}: '
                          'failed to receive current position.')
                return

        self.alpha, self.beta = position

        log.debug(f'positioner {self.positioner_id}: '
                  f'(alpha, beta)={self.alpha, self.beta}')

    async def update_status(self, status=None, timeout=1.):
        """Updates the status of the positioner."""

        if not status:

            command = self.fps.send_command(CommandID.GET_STATUS,
                                            positioner_id=self.positioner_id,
                                            timeout=timeout,
                                            silent_on_conflict=True)

            await command

            if command.status.failed or command.status.timed_out:
                log.error(f'positioner {self.positioner_id}: '
                          f'{CommandID.GET_STATUS.name!r} failed to complete.')
                return False

            if len(command.replies) == 1:
                status = int(bytes_to_int(command.replies[0].data))
            else:
                log.error(f'positioner {self.positioner_id}: '
                          f'{CommandID.GET_STATUS.name!r} received '
                          f'{len(command.replies)} replies.')
                self.status = self.flags.UNKNOWN
                return False

        if not self.is_bootloader():
            self.flags = self.get_position_flags()
        else:
            self.flags = maskbits.BootloaderStatus

        self.status = self.flags(status)

        log.debug(f'positioner {self.positioner_id}: '
                  f'status={self.status.name} ({self.status.value})')

        # Checks if the positioner is collided. If so, locks the FPS.
        if not self.is_bootloader() and self.collision and not self.fps.locked:
            log.error(f'positioner {self.positioner_id} has collided. Locking the FPS.')
            await self.fps.lock()
            return False

        return True

    async def wait_for_status(self, status, delay=1, timeout=None):
        """Polls the status until it reaches a certain value.

        Parameters
        ----------
        status : `~jaeger.maskbits.PositionerStatus`
            The status to wait for. Can be a list in which case it will wait
            until all the statuses in the list have been reached.
        delay : float
            Time, in seconds, to wait between position updates.
        timeout : float
            How many seconds to wait for the status to reach the desired value
            before aborting.

        Returns
        -------
        result : `bool`
            Returns `True` if the status has been reached or `False` if the
            timeout limit was reached.

        """

        if self.is_bootloader():
            log.error('this coroutine cannot be scheduled in bootloader mode.')
            return False

        if not self.fps:
            log.error('no FPS associated with this positioner.')
            return False

        if not isinstance(status, (list, tuple)):
            status = [status]

        async def status_waiter(wait_for_status):

            while True:
                await self.update_status()
                # Check all statuses in the list
                all_reached = True
                for ss in wait_for_status:
                    if ss not in self.status:
                        all_reached = False
                        break

                if all_reached:
                    return

                await asyncio.sleep(delay)

        wait_for_status = [self.flags(ss) for ss in status]

        try:
            await asyncio.wait_for(status_waiter(wait_for_status), timeout)
        except asyncio.TimeoutError:
            return False

        return True

    async def initialise(self, initialise_datums=False):
        """Initialises the datum and starts the position watcher."""

        log.debug(f'positioner {self.positioner_id}: initialising')

        # Resets all.
        await self.reset()

        try:
            await self.update_firmware_version()
        except Exception as ee:
            log.error(f'positioner {self.positioner_id}: failed to update '
                      f'firmware version: {ee}.')
            return False

        result = await self.update_status()
        if not result:
            log.error(f'positioner {self.positioner_id}: failed to refresh status.')
            return False

        # Exists if we are in bootloader mode.
        if self.is_bootloader():
            log.debug(f'positioner {self.positioner_id}: positioner is in bootloader mode.')
            return True

        if initialise_datums:
            result = await self.initialise_datums()
            if not result:
                return False

        if not self.initialised:
            log.error(f'positioner {self.positioner_id}: not initialised. '
                      'Set the position manually.')
            return False

        result = await self.fps.send_command('STOP_TRAJECTORY',
                                             positioner_id=self.positioner_id)
        if not result:
            warnings.warn(f'positioner {self.positioner_id}: failed stopping '
                          'trajectories during initialisation.', JaegerUserWarning)
            return False

        if not result:
            log.error(f'positioner {self.positioner_id}: failed aborting '
                      'trajectory transmission during initialisation.')
            return False

        # Sets the default speed
        if not await self.set_speed(alpha=config['positioner']['motor_speed'],
                                    beta=config['positioner']['motor_speed']):
            return False

        log.debug(f'positioner {self.positioner_id}: initialisation complete.')

        return True

    async def initialise_datums(self):
        """Initialise datums by driving the positioner against hard stops."""

        warnings.warn(f'positioner {self.positioner_id}: reinitialise datums.',
                      JaegerUserWarning)

        result = await self.fps.send_command('INITIALIZE_DATUMS',
                                             positioner_id=self.positioner_id)

        if not result:
            log.error(f'positioner {self.positioner_id}: failed reinitialising datums.')
            return False

        self.status = self.flags.UNKNOWN

        log.info(f'positioner {self.positioner_id}: waiting for datums to initialise.')

        result = await self.wait_for_status(
            [self.flags.DATUM_ALPHA_INITIALIZED,
             self.flags.DATUM_BETA_INITIALIZED],
            timeout=config['positioner']['initialise_datums_timeout'])

        if not result:
            log.error(f'positioner {self.positioner_id}: timeout waiting for '
                      'datums to be reinitialised.')
            return False

        return True

    async def update_firmware_version(self):
        """Updates the firmware version."""

        command = self.fps.send_command(CommandID.GET_FIRMWARE_VERSION,
                                        positioner_id=self.positioner_id)
        await command

        self.firmware = command.get_firmware()
        self.flags = self.get_position_flags()

    def get_position_flags(self):
        """Returns the correct position maskbits from the firmware version."""

        assert self.firmware, 'firmware is not set.'

        if StrictVersion(self.firmware) < StrictVersion('04.01.00'):
            return maskbits.PositionerStatusV4_0
        else:
            return maskbits.PositionerStatusV4_1

    def is_bootloader(self):
        """Returns True if we are in bootloader mode."""

        if self.firmware is None:
            return None

        return self.firmware.split('.')[1] == '80'

    async def set_position(self, alpha, beta):
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

    async def set_speed(self, alpha, beta, force=False):
        """Sets motor speeds.

        Parameters
        ----------
        alpha : float
            The speed of the alpha arm, in RPM on the input.
        beta : float
            The speed of the beta arm, in RPM on the input.
        force : bool
            Allows to set speed limits outside the normal range.

        """

        MIN_SPEED = 0
        MAX_SPEED = 5000

        if (alpha < MIN_SPEED or alpha > MAX_SPEED or
                beta < MIN_SPEED or beta > MAX_SPEED) and not force:
            log.error(f'positioner {self.positioner_id}: speed out of limits.')
            return False

        log.debug(f'positioner {self.positioner_id}: setting speed '
                  f'({float(alpha):.2f}, {float(beta):.2f})')

        speed_command = self.fps.send_command(CommandID.SET_SPEED,
                                              positioner_id=self.positioner_id,
                                              alpha=float(alpha),
                                              beta=float(beta))
        await speed_command

        if speed_command.status.failed:
            return False

        self.speed = [alpha, beta]

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

        if goto_command.status.failed:
            return False

        return goto_command

    async def goto(self, alpha, beta, speed=None, relative=False, force=False):
        """Moves positioner to a given position.

        Parameters
        ----------
        alpha : float
            The position where to move the alpha arm, in degrees.
        beta : float
            The position where to move the beta arm, in degrees.
        speed : tuple
            The speed of the ``(alpha, beta)`` arms, in RPM on the input.
        relative : bool
            Whether the movement is absolute or relative to the current
            position.
        force : bool
            Allows to set position and speed limits outside the normal range.

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
            >>> await goto(speed=(1000, 500))

        """

        async def _restore(original_speed):
            if original_speed != self.speed:
                await self.set_speed(*original_speed)

        ALPHA_MAX_POSITION = 360
        BETA_MAX_POSITION = 360
        ALPHA_MIN_POSITION = -ALPHA_MAX_POSITION if relative else 0
        BETA_MIN_POSITION = -BETA_MAX_POSITION if relative else 0

        if not self.initialised:
            log.error(f'positioner {self.positioner_id}: not initialised.')
            return False

        if not self.fps:
            log.error('the positioner is not linked to a FPS instance.')
            return False

        # Set the speed
        original_speed = self.speed[:]
        if speed and all(speed) and not await self.set_speed(*speed, force=force):
            return False

        # Go to position
        if (alpha < ALPHA_MIN_POSITION or alpha > ALPHA_MAX_POSITION or
                beta < BETA_MIN_POSITION or beta > BETA_MAX_POSITION) and not force:
            log.error(f'positioner {self.positioner_id}: position out of limits.')
            await _restore(original_speed)
            return False

        log.info(f'positioner {self.positioner_id}: goto '
                 f'{"relative" if relative else "absolute"} position '
                 f'({float(alpha):.3f}, {float(beta):.3f}) degrees')

        # Stores the QA information in the DB before the move
        record = self._store_move_qa()
        if record:
            record.alpha_move = alpha
            record.beta_move = beta
            record.relative = relative

        goto_command = await self._goto_position(alpha, beta, relative=relative)

        if not goto_command:
            self._store_move_qa(record, success=False,
                                fail_reason='CAN command failed')
            log.error(f'positioner {self.positioner_id}: '
                      'failed sending the goto position command.')
            await _restore(original_speed)
            return False

        # Sleeps for the time the firmware believes it's going to take
        # to get to the desired position.
        alpha_time, beta_time = goto_command.get_move_time()

        # Update status as soon as we start moving. This clears any possible
        # DISPLACEMENT_COMPLETED.
        await asyncio.sleep(0.1)
        await self.update_status()

        if not self.moving:
            log.info(f'positioner {self.positioner_id}: position reached (did not move).')
            return True

        if not self.moving:
            log.error(f'positioner {self.positioner_id}: positioner is '
                      'not moving when it should.')
            return False

        self.move_time = max([alpha_time, beta_time])

        log.info(f'positioner {self.positioner_id}: '
                 f'the move will take {self.move_time:.2f} seconds')

        await asyncio.sleep(self.move_time)

        # Blocks until we're sure both arms at at the position.
        result = await self.wait_for_status(
            self.flags.DISPLACEMENT_COMPLETED, delay=0.1, timeout=3)

        if result is False:
            self._store_move_qa(record, success=False,
                                fail_reason='Failed to reach position')
            log.error(f'positioner {self.positioner_id}: '
                      'failed to reach commanded position.')
            await _restore(original_speed)
            return False

        log.info(f'positioner {self.positioner_id}: position reached.')

        self._store_move_qa(record, success=True)
        await _restore(original_speed)

        return True

    def _store_move_qa(self, record=None, success=True, fail_reason=''):
        """Stores information about a goto move to the QA DB.

        Parameters
        ----------
        record
            The information is stored in two stages. In the first one, before
            the move, ``record=None`` and a new record is created. In the
            second stage, after the move or if it fails, the previously
            generated record is passed, completed, and saved.
        success : bool
            Whether the move succeeded.
        fail_reason : str
            If ``success=False``, the reason why it failed.

        Returns
        -------
        record
            The DB record, or `None` if there is not a QA database.

        """

        if not self.fps or not self.fps.qa_db:
            return

        if not record:
            Goto = self.fps.qa_db.models['Goto']
            record = Goto()
            record.positioner = self.positioner_id
            record.x_center = self.centre[0] or -999.
            record.y_center = self.centre[1] or -999.
            record.start_time = datetime.datetime.now()
            record.alpha_start = self.position[0]
            record.beta_start = self.position[1]
            record.alpha_speed = self.speed[0]
            record.beta_speed = self.speed[1]
            record.status_start = self.status
            return record

        record.end_time = datetime.datetime.now()
        record.alpha_end = self.position[0]
        record.beta_end = self.position[1]
        record.status_end = self.status

        record.success = success
        if not success:
            record.fail_reason = fail_reason

        record.save(force_insert=True)

        return record

    def __repr__(self):
        status_names = '|'.join([status.name for status in self.status.active_bits])
        return (f'<Positioner (id={self.positioner_id}, '
                f'status={status_names!r}, initialised={self.initialised})>')


class VirtualPositioner(Positioner):
    """Alias for `.Positioner` to be used by the `~jaeger.tests.VirtualFPS`."""

    pass
