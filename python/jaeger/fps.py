#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-19 09:59:11

import asyncio
import os
import pathlib

import astropy.table

from jaeger import config, log, maskbits
from jaeger.can import JaegerCAN
from jaeger.commands import CommandID, send_trajectory
from jaeger.core.exceptions import JaegerUserWarning
from jaeger.positioner import Positioner
from jaeger.utils import bytes_to_int


try:
    from sdssdb.peewee.sdss5db import targetdb
except ImportError:
    targetdb = False


__ALL__ = ['FPS']


class FPS(object):
    """A class describing the Focal Plane System that can be used as an actor.

    Parameters
    ----------
    bus : `.JaegerCAN` instance
        The CAN bus to use.
    layout : str
        A file describing the layout of the FPS. If `None`, the CAN interface
        will be use to determine the positioners connected.
    can_profile : `str` or `None`
        The configuration profile for the CAN interface, or `None` to use the
        default one. Ignored if ``bus`` is passed.
    loop : event loop or `None`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.

    Examples
    --------
    After instantiating a new `.FPS` object it is necessary to call
    `~.FPS.initialise` to retrieve the positioner layout and the status of
    the connected positioners. Note that `~.FPS.initialise` is a coroutine
    which needs to be awaited ::

        >>> fps = FPS(can_profile='default')
        >>> await fps.initialise()
        >>> fps.positioners[4].status
        <Positioner (id=4, status='SYSTEM_INITIALIZATION|
        DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|
        BETA_DISPLACEMENT_COMPLETED')>

    """

    def __init__(self, bus=None, layout=None, can_profile=None, loop=None, **kwargs):

        self.loop = loop or asyncio.get_event_loop()

        if isinstance(bus, JaegerCAN):
            #: The `.JaegerCAN` instance that serves as a CAN bus interface.
            self.bus = bus
        else:
            self.bus = JaegerCAN.from_profile(can_profile, loop=loop)

        self.layout = layout or config['fps']['default_layout']

        #: A list of `~jaeger.positioner.Positioner` instances associated with
        #: this `.FPS` instance.
        self.positioners = {}

        # Loads the positioners from the layout
        self._load_layout(self.layout)

    def __getitem__(self, id):
        """Returns the positioner that correspond to ``id``."""

        return self.positioners[id]

    def _load_layout(self, layout):
        """Loads positioner information from a layout file or DB.

        Parameters
        ----------
        layout : `str` or `pathlib.Path`
            Either the path to a layout file or a string with the layout name
            to be retrieved from the database. If ``layout=None``, retrieves
            the default layout as defined in the config from the DB.

        """

        if isinstance(layout, pathlib.Path) or os.path.exists(layout):

            log.info(f'reading layout from file {layout!s}')

            data = astropy.table.Table.read(layout, format='ascii.no_header',
                                            names=['row', 'pos', 'x', 'y', 'type'])

            pos_id = 1
            for row in data:
                if row['type'].lower() == 'fiducial':
                    continue
                new_positioner = Positioner(pos_id, self, centre=(row['x'], row['y']))
                pos_id += 1
                self.add_positioner(new_positioner)

            n_pos = pos_id - 1

        elif targetdb is not None:

            log.info(f'reading profile {layout} from database')

            if not targetdb.database.connected:
                targetdb.database.connect()
            assert targetdb.database.connected, 'database is not connected.'

            positioners_db = targetdb.Actuator.select().join(
                targetdb.FPSLayout).switch(targetdb.Actuator).join(
                    targetdb.ActuatorType).filter(
                        targetdb.FPSLayout.label == layout,
                        targetdb.ActuatorType.label == 'Robot')

            for pos in positioners_db:
                self.add_positioner(Positioner(pos.id, self,
                                               centre=(pos.xcen, pos.ycen)))

            n_pos = positioners_db.count()

        else:

            raise RuntimeError('database is not available.')

        log.debug(f'loaded positions for {n_pos} positioners')

    def send_command(self, command_id, positioner_id=0, data=[], **kwargs):
        """Sends a command to the bus.

        Parameters
        ----------
        command_id : `str`, `int`, or `~jaeger.commands.CommandID`
            The ID of the command, either as the integer value, a string,
            or the `~jaeger.commands.CommandID` flag
        positioner_id : int
            The positioner ID to command, or zero for broadcast.
        data : bytearray
            The bytes to send.
        kwargs : dict
            Extra arguments to be passed to the command.

        """

        command_flag = CommandID(command_id)
        CommandClass = command_flag.get_command()

        silent_on_conflict = kwargs.pop('silent_on_conflict', False)

        command = CommandClass(positioner_id=positioner_id,
                               bus=self.bus, loop=self.loop,
                               data=data, **kwargs)

        if not command.send(silent_on_conflict=silent_on_conflict):
            return False

        return command

    def add_positioner(self, positioner, **kwargs):
        """Adds a new positioner to the list, and checks for duplicates."""

        assert isinstance(positioner, Positioner), \
            'positioner must be a Positioner instance'

        if positioner.positioner_id in self.positioners:
            raise ValueError(f'there is already a positioner in the list with '
                             f'positioner_id {positioner.positioner_id}.')

        self.positioners[positioner.positioner_id] = positioner

    async def initialise(self, layout=None, check_positioners=True):
        """Initialises all positioners with status and firmware version."""

        # Resets all positioner
        for positioner in self.positioners.values():
            positioner.reset()

        get_status_command = self.send_command(CommandID.GET_STATUS,
                                               positioner_id=0,
                                               timeout=2)
        get_firmware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                 positioner_id=0,
                                                 timeout=2)

        await asyncio.gather(get_status_command, get_firmware_command)

        if get_status_command.status.failed or get_firmware_command.status.failed:
            log.error('failed retrieving status or firmware version.')
            return False

        # Loops over each reply and set the positioner status to OK. If the
        # positioner was not in the list, adds it. Checks how many positioner
        # did not reply.
        for status_reply in get_status_command.replies:

            positioner_id = status_reply.positioner_id
            command_id = status_reply.command_id
            command_name = command_id.name
            response_code = status_reply.response_code

            positioner = self.positioners[positioner_id]

            try:
                positioner.firmware = get_firmware_command.get_firmware(positioner_id)
            except ValueError:
                log.warning(
                    f'({get_firmware_command.command_id.name}, {positioner_id}): '
                    'did not receive a reply. Skipping positioner.',
                    JaegerUserWarning)
                continue

            status_int = int(bytes_to_int(status_reply.data))

            if positioner.is_bootloader():
                status = maskbits.BootloaderStatus(status_int)
                # Need to change the default maskbit flag and initial value
                # to BootloaderStatus and BootloaderStatus.UNKNOWN
                positioner.flags = maskbits.BootloaderStatus
                positioner.status = maskbits.BootloaderStatus.UNKNOWN
            else:
                status = maskbits.PositionerStatus(status_int)

            if positioner_id in self.positioners:
                if response_code == maskbits.ResponseCode.COMMAND_ACCEPTED:
                    positioner.status = status
                else:
                    log.warning(f'({command_name}, {positioner_id}): responded '
                                f' with response code {response_code.name!r}',
                                JaegerUserWarning)
            else:
                log.warning(f'({command_name}, {positioner_id}): replied but '
                            f'if not in the layout. Skipping it.',
                            JaegerUserWarning)
                continue

        n_unknown = len([pos for pos in self.positioners
                         if self[pos].status == maskbits.PositionerStatus.UNKNOWN])

        if n_unknown > 0:
            log.warning(f'{n_unknown} positioners did not respond to '
                        f'{CommandID.GET_STATUS.name!r}', JaegerUserWarning)

        n_non_initialised = len([pos for pos in self.positioners
                                 if (self[pos].status != maskbits.PositionerStatus.UNKNOWN and
                                     not self[pos].initialised)])

        if n_non_initialised > 0:
            log.warning(f'{n_non_initialised} positioners responded but have '
                        'not been initialised.', JaegerUserWarning)

        for positioner in self.positioners.values():
            if positioner.initialised:
                log.debug(f'positioner {positioner.positioner_id}: starting pollers.')
                positioner.start_pollers()

        return True

    async def update_status(self, positioners=None, timeout=1):
        """Update statuses for all positioners.

        Parameters
        ----------
        positioners : `list`
            The list of positioners to update. If `None`, update all
            positioners.
        timeout : float
            How long to wait before timing out the command.

        """

        if positioners is None:
            positioners = list(self.positioners.keys())

        await asyncio.gather(
            *[self.positioners[pid].update_status(timeout=timeout)
              for pid in positioners], loop=self.loop)

    def start_pollers(self, poller='all'):
        """Starts the pollers for all valid positioners.

        Parameters
        ----------
        poller : str
            Either ``'position'`` or ``'status'`` to start the position or
            status pollers, or ``'all'`` to start both.

        """

        for positioner in self.positioners.values():
            if positioner.initialised:
                positioner.start_pollers(poller=poller)

    def stop_pollers(self, poller='all'):
        """Stops the pollers for all valid positioners.

        Parameters
        ----------
        poller : str
            Either ``'position'`` or ``'status'`` to start the position or
            status pollers, or ``'all'`` to start both.

        """

        for positioner in self.positioners.values():
            positioner.stop_pollers(poller=poller)

    async def _abort_trajectory(self, positioners=None, timeout=1):
        """Sends ``STOP_TRAJECTORY`` to all positioners.

        Parameters
        ----------
        positioners : `list`
            The list of positioners to abort. If `None`, abort all positioners.
        timeout : float
            How long to wait before timing out the command.

        """

        if positioners is None:
            await self.send_command('STOP_TRAJECTORY', positioner_id=0,
                                    timeout=timeout)
            return

        await asyncio.gather(
            *[self.send_command('STOP_TRAJECTORY', positioner_id=pid,
                                timeout=timeout)
              for pid in positioners], loop=self.loop)

    async def send_trajectory(self, *args, **kwargs):
        """Sends a set of trajectories to the positioners.

        See the documentation for `.send_trajectory`.

        """

        return await send_trajectory(self, *args, **kwargs)

    def abort(self):
        """Aborts trajectories and stops positioners."""

        cmd = self.send_command(CommandID.STOP_TRAJECTORY, positioner_id=0)
        return asyncio.create_task(cmd)

    async def send_to_all(self, command, positioners=None, data=None):
        """Sends a command to multiple positioners and awaits completion.

        Parameters
        ----------
        command : str
            The name of the command to send.
        positioners : list
            The list of ``positioner_id`` of the positioners to command. If
            `None`, sends the command to all the positioners in the FPS.
        data : list
            The payload to send. If `None`, no payload is sent. If the value
            is a list with a single value, the same payload is sent to all
            the positioners. Otherwise the list length must match the number
            of positioners.

        Returns
        -------
        commands : `list`
            A list with the command instances executed.

        """

        positioners = positioners or list(self.positioners.keys())

        if data is None or len(data) == 1:
            commands = [self.send_command(command, positioner_id=positioner_id)
                        for positioner_id in positioners]
        else:
            commands = [self.send_command(command, positioner_id=positioner_id,
                                          data=data[ii])
                        for ii, positioner_id in enumerate(positioners)]

        await asyncio.gather(*commands)

        return commands

    async def shutdown(self):

        log.info('stopping all pollers.')

        self.stop_pollers()
        await asyncio.sleep(1)

        log.info('cancelling all pending tasks and shutting down.')

        tasks = [task for task in asyncio.Task.all_tasks()
                 if task is not asyncio.tasks.Task.current_task()]
        list(map(lambda task: task.cancel(), tasks))

        await asyncio.gather(*tasks, return_exceptions=True)

        self.loop.stop()

    def start_actor(self):
        """Initialises the actor."""

        raise NotImplementedError('the actor functionality '
                                  'has not yet been implemented.')
