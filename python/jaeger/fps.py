#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import os
import asyncio
import pathlib
import warnings

import astropy.table

from jaeger import config, log, maskbits
from jaeger.can import JaegerCAN
from jaeger.commands import Command, CommandID, send_trajectory
from jaeger.core.exceptions import FPSLockedError, JaegerUserWarning
from jaeger.positioner import Positioner
from jaeger.utils import Poller, PollerList, bytes_to_int, get_qa_database
from jaeger.wago import WAGO


try:
    from sdssdb.peewee.sdss5db import targetdb
except ImportError:
    targetdb = False


__ALL__ = ['BaseFPS', 'FPS']


class BaseFPS(object):
    """A class describing the Focal Plane System.

    This class includes methods to read the layout and construct positioner
    objects and can be used by the real `FPS` class or the
    `~jaeger.testing.VirtualFPS`.

    Parameters
    ----------
    layout : str
        The layout describing the position of the robots on the focal plane.
        If `None`, the default layout will be used. Can be either a layout name
        to be recovered from the database, or a file path to the layout
        configuration.
    positioner_class : class
        The class to be used to create a new positioner. In principle this will
        be `.Positioner` but it may be different if the positioners are created
        for a `~jaeger.testing.VirtualFPS`.

    """

    def __init__(self, layout=None, positioner_class=Positioner):

        self._class_name = self.__class__.__name__

        self.layout = layout or config['fps']['default_layout']

        #: A list of `~jaeger.positioner.Positioner` instances associated with
        #: this `.FPS` instance.
        self.positioners = {}

        self._positioner_class = positioner_class

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
            the default layout as defined in the config from the DB. If
            no DB is present and no layout file is provided, loads an empty
            layout to which connected positioners will be added but without
            spatial information.

        """

        if isinstance(layout, pathlib.Path) or os.path.exists(layout):

            log.info(f'{self._class_name}: reading layout from file {layout!s}.')

            data = astropy.table.Table.read(layout, format='ascii.no_header',
                                            names=['id', 'row', 'pos', 'x', 'y', 'type'])

            for row in data:
                if row['type'].lower() == 'fiducial':
                    continue
                self.add_positioner(row['id'], centre=(row['x'], row['y']))

            n_pos = len(self.positioners)

        elif targetdb:

            log.info(f'{self._class_name}: reading profile {layout!r} from database.')

            if not targetdb.database.connected:
                targetdb.database.connect()
            assert targetdb.database.connected, \
                f'{self._class_name}: database is not connected.'

            positioners_db = targetdb.Actuator.select().join(
                targetdb.FPSLayout).switch(targetdb.Actuator).join(
                    targetdb.ActuatorType).filter(
                        targetdb.FPSLayout.label == layout,
                        targetdb.ActuatorType.label == 'Robot')

            for pos in positioners_db:
                self.add_positioner(pos.id, centre=(pos.xcen, pos.ycen))

            n_pos = positioners_db.count()

        else:

            n_pos = 0
            warnings.warn('no layout was provided. Loading an empty FPS.',
                          JaegerUserWarning)

        log.debug(f'{self._class_name}: loaded positions for {n_pos} positioners.')

    def add_positioner(self, positioner_id, centre=(None, None)):
        """Adds a new positioner to the list, and checks for duplicates."""

        if positioner_id in self.positioners:
            raise ValueError(f'{self._class_name}: there is already a '
                             f'positioner in the list with positioner_id '
                             f'{positioner_id}.')

        self.positioners[positioner_id] = self._positioner_class(positioner_id, self,
                                                                 centre=centre)

        if self.qa_db:

            Positioner = self.qa_db.models['Positioner']

            # Check if the positioner exists.
            db_pos = Positioner.select().filter(Positioner.id == positioner_id).first()

            if not db_pos:
                new = True
                db_pos = Positioner(id=positioner_id)
            else:
                new = False

            db_pos.x_center = centre[0] or -999.
            db_pos.y_center = centre[1] or -999.

            db_pos.save(force_insert=new)

    def report_status(self):
        """Returns a dict with the position and status of each positioner."""

        status = {}

        for positioner in self.positioners.values():

            pos_status = positioner.status
            pos_firmware = positioner.firmware
            pos_alpha = positioner.alpha
            pos_beta = positioner.beta

            status[positioner.positioner_id] = {'position': [pos_alpha, pos_beta],
                                                'status': pos_status,
                                                'firmware': pos_firmware}

        try:
            status['devices'] = self.can.device_status
        except AttributeError:
            pass

        return status


class FPS(BaseFPS):
    """A class describing the Focal Plane System.

    Parameters
    ----------
    can : `.JaegerCAN` instance
        The CAN bus to use.
    layout : str
        The layout describing the position of the robots on the focal plane.
        If `None`, the default layout will be used. Can be either a layout name
        to be recovered from the database, or a file path to the layout
        configuration.
    can_profile : str or None
        The configuration profile for the CAN interface, or `None` to use the
        default one. Ignored if ``can`` is passed.
    wago : bool or .WAGO
        If `True`, connects the WAGO PLC controller.
    qa : bool or path
        A path to the database used to store QA information. If `True`, uses
        the value from ``config.files.qa_database``. If `False`, does not do
        any QA recording.
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

    def __init__(self, can=None, layout=None, can_profile=None,
                 wago=None, qa=None, loop=None):

        self.loop = loop or asyncio.get_event_loop()

        #: dict: The mapping between positioners and buses.
        self.positioner_to_bus = {}

        if isinstance(can, JaegerCAN):
            #: The `.JaegerCAN` instance that serves as a CAN bus interface.
            self.can = can
        else:
            try:
                self.can = JaegerCAN.from_profile(can_profile, fps=self, loop=loop)
            except ConnectionRefusedError:
                raise

        self._locked = False

        #: .WAGO: The WAGO PLC system that controls the FPS.
        self.wago = None

        if wago is None:
            wago = config['fps']['wago']

        if isinstance(wago, WAGO):
            self.wago = wago
        elif wago is True:
            self.wago = WAGO.from_config()
        else:
            self.wago = False

        if qa is None:
            qa = config['fps']['qa']

        if qa is True:
            self.qa_db = get_qa_database(config['files']['qa_database'])
        elif qa is False:
            self.qa_db = None
        else:
            self.qa_db = get_qa_database(qa)

        super().__init__(layout=layout)

        self.pollers = PollerList([
            Poller('status', self.update_status,
                   delay=config['fps']['status_poller_delay'],
                   loop=self.loop),
            Poller('position', self.update_position,
                   delay=config['fps']['position_poller_delay'],
                   loop=self.loop)
        ])

    async def _get_positioner_bus_map(self):
        """Creates the positioner-to-bus map.

        Only relevant if the bus interface is multichannel/multibus.

        """

        if len(self.can.interfaces) == 1 and not self.can.multibus:
            self._is_multibus = False
            return

        self._is_multibus = True

        id_cmd = self.send_command(CommandID.GET_ID, timeout=0.5)
        await id_cmd

        # Parse the replies
        for reply in id_cmd.replies:
            self.positioner_to_bus[reply.positioner_id] = (reply.message.interface,
                                                           reply.message.bus)

    def send_command(self, command, positioner_id=0, data=[],
                     interface=None, bus=None, broadcast=False,
                     silent_on_conflict=False, override=False,
                     safe=False, **kwargs):
        """Sends a command to the bus.

        Parameters
        ----------
        command : str, int, .CommandID or .Command
            The ID of the command, either as the integer value, a string,
            or the `.CommandID` flag. Alternatively, the `.Command` to send.
        positioner_id : int
            The positioner ID to command, or zero for broadcast.
        data : bytearray
            The bytes to send.
        interface : int
            The index in the interface list for the interface to use. Only
            relevant in case of a multibus interface. If `None`, the positioner
            to bus map will be used.
        bus : int
            The bus within the interface to be used. Only relevant in case of
            a multibus interface. If `None`, the positioner to bus map will
            be used.
        broadcast : bool
            If `True`, sends the command to all the buses.
        silent_on_conflict : bool
            If set, does not issue a warning if at the time of queuing this
            command there is already a command for the same positioner id
            running. This is useful for example for poller when we change the
            delay and the previous command is still running. In those cases
            this option avoids annoying messages.
        override : bool
            If another instance of this command_id with the same positioner_id
            is running, cancels it and schedules this one immediately.
            Otherwise the command is queued until the first one finishes.
        safe : bool
            Whether the command is safe to send to a locked `.FPS`.
        kwargs : dict
            Extra arguments to be passed to the command.

        Returns
        -------
        command : `.Command`
            The command sent to the bus. The command needs to be awaited
            before it is considered done.

        """

        if positioner_id == 0:
            broadcast = True

        if not isinstance(command, Command):
            command_flag = CommandID(command)
            CommandClass = command_flag.get_command()

            command = CommandClass(positioner_id=positioner_id,
                                   loop=self.loop, data=data, **kwargs)

        command_name = command.name

        if self.locked:
            if command.safe or safe:
                log.debug(f'FPS is locked but {command_name} is safe.')
            else:
                command.cancel(silent=True)
                raise FPSLockedError('solve the problem and unlock the FPS '
                                     'before sending commands.')

        if command.status.is_done:
            log.error(f'{command_name, positioner_id}: trying to send a done command.')
            return False

        command._override = override
        command._silent_on_conflict = silent_on_conflict

        # By default a command will be sent to all interfaces and buses.
        # Normally we want to set the interface and bus to which the command
        # will be sent.
        if not broadcast:
            self.set_interface(command, bus=bus, interface=interface)
            if command.status == command.status.FAILED:
                return command

        self.can.command_queue.put_nowait(command)
        log.debug(f'{command_name, positioner_id}: added command to CAN processing queue.')

        return command

    def set_interface(self, command, interface=None, bus=None):
        """Sets the interface and bus to which to send a command."""

        # Don't do anything if the interface is not multibus
        if not self._is_multibus or command.positioner_id == 0:
            return

        if bus or interface:
            command._interface = interface
            command._bus = bus
            return

        if command.positioner_id not in self.positioner_to_bus:
            log.error(f'positioner {command.positioner_id} has no assigned bus.')
            command.finish_command(command.status.FAILED)
            return

        interface, bus = self.positioner_to_bus[command.positioner_id]

        command._interface = interface
        command._bus = bus

        return

    @property
    def locked(self):
        """Returns `True` if the `.FPS` is locked."""

        return self._locked

    async def lock(self, abort_trajectories=True):
        """Locks the `.FPS` and prevents commands to be sent.

        Parameters
        ----------
        abort_trajectories : bool
            Whether to abort trajectories when locking.

        """

        warnings.warn('locking FPS.', JaegerUserWarning)
        self._locked = True

        if abort_trajectories:
            await self.abort_trajectory()

    def unlock(self, force=False):
        """Unlocks the `.FPS` if all collisions have been resolved."""

        for positioner in self.positioners.values():
            if positioner.collision:
                self._locked = True
                log.error('cannot unlock the FPS until all '
                          'the collisions have been cleared.')
                return False

        self._locked = False

        return True

    async def initialise(self, allow_unknown=True):
        """Initialises all positioners with status and firmware version.

        Parameters
        ----------
        allow_unknown : bool
            If `True`, allows to add positioners that are connected but not
            in the layout.

        """

        unknwon_positioners = []

        # Start by initialising the WAGO.
        if self.wago and not self.wago.connected:

            try:
                await self.wago.connect()
                log.info(f'WAGO connected on host {self.wago.client.host}')
            except RuntimeError as ee:
                log.error(f'failed to initialise WAGO: {ee}')

        # Get the positioner-to-bus map
        await self._get_positioner_bus_map()

        # Resets all positioner
        for positioner in self.positioners.values():
            await positioner.reset()

        # Stop poller in case they are running
        await self.pollers.stop()

        if len(self.positioners) > 0:
            n_expected_positioners = len(self.positioners)
        else:
            n_expected_positioners = None

        get_firmware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                 positioner_id=0,
                                                 timeout=0.5,
                                                 n_positioners=n_expected_positioners)

        await get_firmware_command

        if get_firmware_command.status.failed:
            log.error('failed retrieving firmware version.')
            return False

        # Loops over each reply and set the positioner status to OK. If the
        # positioner was not in the list, adds it. Checks how many positioner
        # did not reply.
        for reply in get_firmware_command.replies:

            positioner_id = reply.positioner_id

            if positioner_id not in self.positioners:
                if allow_unknown:
                    unknwon_positioners.append(positioner_id)
                    self.add_positioner(positioner_id)
                else:
                    log.error(f'found positioner with ID={positioner_id} '
                              'that is not in the layout.')
                    return False

            positioner = self.positioners[positioner_id]
            positioner.firmware = get_firmware_command.get_firmware(positioner_id)

        await self.update_status(timeout=0.5)

        if len(unknwon_positioners) > 0:
            warnings.warn(f'found {len(unknwon_positioners)} unknown positioners '
                          f'with IDs {sorted(unknwon_positioners)!r}. '
                          'They have been added to the layout.', JaegerUserWarning)

        n_did_not_reply = len([pos for pos in self.positioners
                               if self[pos].status == maskbits.PositionerStatus.UNKNOWN])

        if n_did_not_reply > 0:
            warnings.warn(f'{n_did_not_reply} positioners did not respond to '
                          f'{CommandID.GET_STATUS.name!r}', JaegerUserWarning)

        n_non_initialised = len([pos for pos in self.positioners
                                 if (self[pos].status != maskbits.PositionerStatus.UNKNOWN and
                                     not self[pos].initialised)])

        if n_non_initialised > 0:
            warnings.warn(f'{n_non_initialised} positioners responded but have '
                          'not been initialised.', JaegerUserWarning)

        if self.locked:
            log.info('FPS is locked. Trying to unlock it.')
            if not self.unlock():
                log.error('FPS cannot be unlocked. Initialisation failed.')
                return False
            else:
                log.info('FPS unlocked successfully.')

        initialise_cmds = [positioner.initialise()
                           for positioner in self.positioners.values()
                           if positioner.status != maskbits.PositionerStatus.UNKNOWN]
        results = await asyncio.gather(*initialise_cmds)

        if False in results:
            log.error('some positioners failed to initialise.')
            return False

        # Start the pollers
        self.pollers.start()

        return True

    async def update_status(self, positioner_id=None, timeout=1):
        """Update statuses for all positioners.

        Parameters
        ----------
        positioners : `list`
            The list of positioners to update. If `None`, update all
            positioners.
        timeout : float
            How long to wait before timing out the command.

        """

        if positioner_id:
            n_positioners = len(positioner_id)
        else:
            n_positioners = None

        command = self.send_command(CommandID.GET_STATUS, positioner_id=0,
                                    n_positioners=n_positioners,
                                    timeout=timeout)
        await command

        if command.status.failed:
            log.warning(f'failed broadcasting {CommandID.GET_STATUS.name!r} '
                        'during update status.')
            return

        update_status_coros = []
        for reply in command.replies:

            pid = reply.positioner_id
            if pid not in self.positioners:
                continue

            positioner = self.positioners[pid]

            status_int = int(bytes_to_int(reply.data))
            update_status_coros.append(positioner.update_status(status_int))

        await asyncio.gather(*update_status_coros)

        return

    async def update_position(self, positioner_id=None):
        """Updates positions."""

        if not positioner_id:
            positioner_id = [pid for pid in self.positioners
                             if self[pid].initialised and
                             not self[pid].is_bootloader()]
            if not positioner_id:
                return

        commands_all = self.send_to_all(CommandID.GET_ACTUAL_POSITION,
                                        positioners=positioner_id)

        try:
            commands = await commands_all
        except Exception as ee:
            log.error(f'failed polling positions: {ee}')
            return

        update_position_commands = []
        for command in commands:

            pid = command.positioner_id

            if command.status.failed and self[pid].initialised:
                log.warning(f'({CommandID.GET_ACTUAL_POSITION.name}, '
                            f'{command.positioner_id}): '
                            'failed during update position.')
                continue

            position = command.get_positions()
            update_position_commands.append(self[pid].update_position(position))

        await asyncio.gather(*update_position_commands)

        return

    async def update_firmware_version(self, positioner_id=None):
        """Updates the firmware version of connected positioners."""

        if positioner_id:
            n_positioners = len(positioner_id)
        else:
            n_positioners = None

        get_firmware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                 positioner_id=0,
                                                 timeout=2,
                                                 n_positioners=n_positioners)

        await get_firmware_command

        if get_firmware_command.status.failed:
            log.error('failed retrieving firmware version.')
            return False

        for reply in get_firmware_command.replies:
            positioner_id = reply.positioner_id
            positioner = self.positioners[positioner_id]
            positioner.firmware = get_firmware_command.get_firmware(positioner_id)

    async def abort_trajectory(self, positioners=None, timeout=1):
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

    async def send_to_all(self, command, positioners=None, data=None, **kwargs):
        """Sends a command to multiple positioners and awaits completion.

        Parameters
        ----------
        command : str, int, .CommandID or .Command
            The ID of the command, either as the integer value, a string,
            or the `.CommandID` flag. Alternatively, the `.Command` to send.
        positioners : list
            The list of ``positioner_id`` of the positioners to command. If
            `None`, sends the command to all the positioners in the FPS.
        data : list
            The payload to send. If `None`, no payload is sent. If the value
            is a list with a single value, the same payload is sent to all
            the positioners. Otherwise the list length must match the number
            of positioners.
        kwargs : dict
            Keyword argument to pass to the command.

        Returns
        -------
        commands : `list`
            A list with the command instances executed.

        """

        positioners = positioners or list(self.positioners.keys())

        if data is None or len(data) == 1:
            commands = [self.send_command(command, positioner_id=positioner_id, **kwargs)
                        for positioner_id in positioners]
        else:
            commands = [self.send_command(command, positioner_id=positioner_id,
                                          data=data[ii], **kwargs)
                        for ii, positioner_id in enumerate(positioners)]

        results = await asyncio.gather(*commands, return_exceptions=True)

        if any([isinstance(rr, FPSLockedError) for rr in results]):
            raise FPSLockedError('one or more of the commands failed because '
                                 'the FPS is locked.')

        return commands

    async def shutdown(self):
        """Stops pollers and shuts down all remaining tasks."""

        log.info('stopping all pollers.')

        await self.pollers.stop()

        await asyncio.sleep(1)

        log.info('cancelling all pending tasks and shutting down.')

        tasks = [task for task in asyncio.all_tasks(loop=self.loop)
                 if task is not asyncio.current_task(loop=self.loop)]
        list(map(lambda task: task.cancel(), tasks))

        await asyncio.gather(*tasks, return_exceptions=True)

        self.loop.stop()
