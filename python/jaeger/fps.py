#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import os
import pathlib
import warnings

import astropy.table

from drift import Drift, DriftError

from jaeger import CONFIG_FILE, config, log
from jaeger.can import JaegerCAN
from jaeger.commands import Command, CommandID, send_trajectory
from jaeger.exceptions import FPSLockedError, JaegerUserWarning, TrajectoryError
from jaeger.positioner import Positioner
from jaeger.utils import Poller, PollerList, bytes_to_int, get_qa_database


# try:
#     from sdssdb.peewee.sdss5db import targetdb
# except ImportError:
#     targetdb = False


__ALL__ = ['BaseFPS', 'FPS', 'IEB']


class IEB(Drift):
    """Thing wrapper around a :class:`~drift.drift.Drift` class.

    Allows additional features such as disabling the interface.

    """

    def __init__(self, *args, **kwargs):

        self.disabled = False

        super().__init__(*args, **kwargs)

    async def __aenter__(self):

        if self.disabled:
            raise DriftError('IEB is disabled.')

        try:
            await Drift.__aenter__(self)
        except DriftError:
            self.disabled = True
            warnings.warn('Failed connecting to the IEB. Disabling it.',
                          JaegerUserWarning)

    async def __aexit__(self, *args):

        await Drift.__aexit__(self, *args)


class BaseFPS(dict):
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

        dict.__init__(self, {})

        self._positioner_class = positioner_class

        # Loads the positioners from the layout
        self._load_layout(self.layout)

    @property
    def positioners(self):
        """Dictionary of positioner associated with this FPS.

        This is just a wrapper around the `.BaseFPS` instance which is a
        dictionary itself. May be deprecated in the future.

        """

        return self

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

        # elif targetdb and targetdb.database.connected:

        #     log.info(f'{self._class_name}: reading profile {layout!r} from database.')

        #     if not targetdb.database.connected:
        #         targetdb.database.connect()
        #     assert targetdb.database.connected, \
        #         f'{self._class_name}: database is not connected.'

        #     positioners_db = targetdb.Actuator.select().join(
        #         targetdb.FPSLayout).switch(targetdb.Actuator).join(
        #             targetdb.ActuatorType).filter(
        #                 targetdb.FPSLayout.label == layout,
        #                 targetdb.ActuatorType.label == 'Robot')

        #     for pos in positioners_db:
        #         self.add_positioner(pos.id, centre=(pos.xcen, pos.ycen))

        #     n_pos = positioners_db.count()

        else:

            n_pos = 0
            warnings.warn(f'cannot retrieve layout {layout!r} from the database. '
                          'targetdb may be down. Loading an empty FPS.',
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
    ieb : bool or .IEB instance
        If `True`, connects the Instrument Electronics Box PLC controller.
    qa : bool or path
        A path to the database used to store QA information. If `True`, uses
        the value from ``config.files.qa_database``. If `False`, does not do
        any QA recording.
    loop : event loop or `None`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.
    engineering_mode : bool
        If `True`, disables most safety checks to enable debugging. This may
        result in hardware damage so it must not be used lightly.

    Examples
    --------
    After instantiating a new `.FPS` object it is necessary to call
    `~.FPS.initialise` to retrieve the positioner layout and the status of
    the connected positioners. Note that `~.FPS.initialise` is a coroutine
    which needs to be awaited ::

        >>> fps = FPS(can_profile='default')
        >>> await fps.initialise()
        >>> fps.positioners[4].status
        <Positioner (id=4, status='SYSTEM_INITIALIZED|
        DISPLACEMENT_COMPLETED|ALPHA_DISPLACEMENT_COMPLETED|
        BETA_DISPLACEMENT_COMPLETED')>

    """

    def __init__(self, can=None, layout=None, can_profile=None,
                 ieb=None, qa=None, loop=None, engineering_mode=False):

        if CONFIG_FILE:
            log.info(f'using configuration from {CONFIG_FILE}')
        else:
            log.warning('cannot find SDSSCORE or user configuration. Using default values.')

        self.engineering_mode = engineering_mode

        if engineering_mode:
            warnings.warn('Engineering mode enable. Please don\'t break anything.',
                          JaegerUserWarning)

        self.loop = loop or asyncio.get_event_loop()
        self.loop.set_exception_handler(log.asyncio_exception_handler)

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

        #: .IEB: Connection to the instrument electronics box over Modbus.
        self.ieb = None

        if ieb is None or ieb is True:
            ieb = config['fps']['ieb']

        if isinstance(ieb, IEB):
            self.ieb = ieb
        elif isinstance(ieb, (str, dict)):
            if isinstance(ieb, str):
                ieb = os.path.expanduser(os.path.expandvars(ieb))
            self.ieb = IEB.from_config(ieb)
        elif ieb is False:
            self.ieb = False
        else:
            raise ValueError(f'Invalid input value for ieb {ieb!r}.')

        if qa is None:
            qa = config['fps']['qa']

        if qa is True:
            self.qa_db = get_qa_database(config['files']['qa_database'])
        elif qa is False:
            self.qa_db = None
        else:
            self.qa_db = get_qa_database(qa)

        super().__init__(layout=layout)

        #: Position and status pollers
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

        id_cmd = self.send_command(CommandID.GET_ID,
                                   timeout=config['fps']['initialise_timeouts'])
        await id_cmd

        # Parse the replies
        for reply in id_cmd.replies:
            self.positioner_to_bus[reply.positioner_id] = (reply.message.interface,
                                                           reply.message.bus)

    def send_command(self, command, positioner_id=0, data=[],
                     interface=None, bus=None, broadcast=False,
                     silent_on_conflict=False, override=False,
                     safe=False, synchronous=False, **kwargs):
        """Sends a command to the bus.

        Parameters
        ----------
        command : str, int, .CommandID, or .Command
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
        synchronous : bool
            If `True`, the command is sent to the CAN network immediately,
            skipping the command queue. No tracking is done for this command.
            It should only be used for shutdown commands.
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

        if not self.engineering_mode and self.locked:
            if command.safe or safe:
                log.debug(f'FPS is locked but {command_name} is safe.')
            else:
                command.cancel(silent=True)
                raise FPSLockedError('solve the problem and unlock the FPS '
                                     'before sending commands.')

        elif not self.engineering_mode and command.move_command and self.moving:
            command.cancel(silent=True)
            log.error('cannot send move command while the FPS is moving. '
                      'Use FPS.stop_trajectory() to stop the FPS.')
            return command

        if command.status.is_done:
            log.error(f'({command_name}, {positioner_id}): trying to send a done command.')
            return command

        command._override = override
        command._silent_on_conflict = silent_on_conflict

        # By default a command will be sent to all interfaces and buses.
        # Normally we want to set the interface and bus to which the command
        # will be sent.
        if not broadcast:
            self.set_interface(command, bus=bus, interface=interface)
            if command.status == command.status.FAILED:
                return command

        if not synchronous:
            self.can.command_queue.put_nowait(command)
            log.debug(f'({command_name}, {positioner_id}): '
                      'added command to CAN processing queue.')
        else:
            self.can._send_messages(command)
            log.debug(f'({command_name}, {positioner_id}): '
                      'sent command to CAN synchronously.')

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

    async def lock(self, stop_trajectories=True):
        """Locks the `.FPS` and prevents commands to be sent.

        Parameters
        ----------
        stop_trajectories : bool
            Whether to stop trajectories when locking.

        """

        warnings.warn('locking FPS.', JaegerUserWarning)
        self._locked = True

        if stop_trajectories:
            await self.stop_trajectory()

    async def unlock(self, force=False):
        """Unlocks the `.FPS` if all collisions have been resolved."""

        await self.update_status(timeout=0.1)

        for positioner in self.positioners.values():
            if positioner.collision and not self.engineering_mode:
                self._locked = True
                log.error('cannot unlock the FPS until all '
                          'the collisions have been cleared.')
                return False

        self._locked = False

        return True

    @property
    def moving(self):
        """Returns `True` if any of the positioners is moving."""

        return any([pos.moving for pos in self.values()
                    if pos.status != pos.flags.UNKNOWN])

    async def initialise(self, allow_unknown=True, start_pollers=True):
        """Initialises all positioners with status and firmware version.

        Parameters
        ----------
        allow_unknown : bool
            If `True`, allows to add positioners that are connected but not
            in the layout.

        """

        unknwon_positioners = []

        # Test IEB connection. This will issue a warning and set
        # self.ieb.disabled=True if the connection fails.
        if self.ieb:
            async with self.ieb:
                pass

        # Get the positioner-to-bus map
        await self._get_positioner_bus_map()

        # Resets all positioners
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
                                                 timeout=config['fps']['initialise_timeouts'],
                                                 n_positioners=n_expected_positioners)

        await get_firmware_command

        if get_firmware_command.status.failed:
            if not self.engineering_mode:
                log.error('failed retrieving firmware version. '
                          'Cannot initialise FPS.')
                return False
            else:
                warnings.warn('failed retrieving firmware version. '
                              'Continuing because engineering mode.', JaegerUserWarning)

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

        if len(set([pos.firmware for pos in self.values()])) > 1:
            warnings.warn('positioners with different firmware versions found.',
                          JaegerUserWarning)

        # Stop positioners that are not in bootloader mode.
        await self.stop_trajectory()

        await self.update_status(timeout=config['fps']['initialise_timeouts'])

        if len(unknwon_positioners) > 0:
            warnings.warn(f'found {len(unknwon_positioners)} unknown positioners '
                          f'with IDs {sorted(unknwon_positioners)!r}. '
                          'They have been added to the layout.', JaegerUserWarning)

        n_did_not_reply = len([pos for pos in self.positioners
                               if self[pos].status == self[pos].flags.UNKNOWN])

        if n_did_not_reply > 0:
            warnings.warn(f'{n_did_not_reply} positioners did not respond to '
                          f'{CommandID.GET_STATUS.name!r}', JaegerUserWarning)

        n_non_initialised = len([pos for pos in self.positioners
                                 if (self[pos].status != self[pos].flags.UNKNOWN and
                                     not self[pos].initialised)])

        if n_non_initialised > 0:
            warnings.warn(f'{n_non_initialised} positioners responded but have '
                          'not been initialised.', JaegerUserWarning)

        if self.locked:
            log.info('FPS is locked. Trying to unlock it.')
            if not await self.unlock():
                log.error('FPS cannot be unlocked. Initialisation failed.')
                return False
            else:
                log.info('FPS unlocked successfully.')

        # This may not be techincally necessary but it's just a few messages ...
        initialise_cmds = [positioner.initialise()
                           for positioner in self.positioners.values()
                           if positioner.status != positioner.flags.UNKNOWN]
        results = await asyncio.gather(*initialise_cmds)

        if False in results:
            log.error('some positioners failed to initialise.')
            if self.engineering_mode:
                warnings.warn('continuing because engineering mode ...',
                              JaegerUserWarning)

        await self.update_position()

        # Start the pollers
        if start_pollers:
            self.pollers.start()

        return self

    async def update_status(self, positioner_ids=None, timeout=1):
        """Update statuses for all positioners.

        Parameters
        ----------
        positioner_ids : list
            The list of positioners to update. If `None`, update all
            positioners. ``positioner_ids=False`` ignores currently
            connected positioners and times out to receive all possible
            replies.
        timeout : float
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if positioner_ids:
            n_positioners = len(positioner_ids)
        elif positioner_ids is None:
            # This is the max number that should reply.
            n_positioners = len(self) if len(self) > 0 else None

        await self.update_firmware_version(timeout=timeout)

        command = self.send_command(CommandID.GET_STATUS, positioner_id=0,
                                    n_positioners=n_positioners,
                                    timeout=timeout,
                                    override=True,
                                    silent_on_conflict=True)
        await command

        if command.status.failed:
            log.warning(f'failed broadcasting {CommandID.GET_STATUS.name!r} '
                        'during update status.')
            return False

        update_status_coros = []
        for reply in command.replies:

            pid = reply.positioner_id
            if pid not in self.positioners or (positioner_ids and pid not in positioner_ids):
                continue

            positioner = self.positioners[pid]

            status_int = int(bytes_to_int(reply.data))
            update_status_coros.append(positioner.update_status(status_int))

        await asyncio.gather(*update_status_coros)

        return True

    async def update_position(self, positioner_ids=None, timeout=1):
        """Updates positions.

        Parameters
        ----------
        positioner_ids : list
            The list of positioners to update. If `None`, update all
            positioners.
        timeout : float
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if not positioner_ids:
            positioner_ids = [pid for pid in self.positioners
                              if self[pid].initialised and
                              not self[pid].is_bootloader()]
            if not positioner_ids:
                return True

        commands_all = self.send_to_all(CommandID.GET_ACTUAL_POSITION,
                                        positioners=positioner_ids,
                                        timeout=timeout)

        commands = await commands_all

        update_position_commands = []
        for command in commands:

            pid = command.positioner_id

            if (not isinstance(command, Command) or
                    (command.status.failed and self[pid].initialised)):
                log.warning(f'({CommandID.GET_ACTUAL_POSITION.name}, '
                            f'{command.positioner_id}): '
                            'failed during update position.')
                continue

            try:
                position = command.get_positions()
                update_position_commands.append(self[pid].update_position(position))
            except ValueError as ee:
                log.error(f'failed updating position for positioner {pid}: {ee}')
                return False

        await asyncio.gather(*update_position_commands)

        return True

    async def update_firmware_version(self, positioner_ids=None, timeout=2):
        """Updates the firmware version of connected positioners.

        Parameters
        ----------
        positioner_ids : list
            The list of positioners to update. If `None`, update all
            positioners. ``positioner_ids=False`` ignores currently
            connected positioners and times out to receive all possible
            replies.
        timeout : float
            How long to wait before timing out the command.

        """

        assert not positioner_ids or isinstance(positioner_ids, (list, tuple))

        if positioner_ids:
            n_positioners = len(positioner_ids)
        else:
            n_positioners = len(self) if len(self) > 0 else None

        get_firmware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                 positioner_id=0,
                                                 timeout=timeout,
                                                 n_positioners=n_positioners)

        await get_firmware_command

        if get_firmware_command.status.failed:
            log.error('failed retrieving firmware version.')
            return False

        for reply in get_firmware_command.replies:
            pid = reply.positioner_id
            if pid not in self.positioners or (positioner_ids and pid not in positioner_ids):
                continue

            positioner = self.positioners[pid]
            positioner.firmware = get_firmware_command.get_firmware(pid)

        return True

    async def stop_trajectory(self, positioners=None, clear_flags=True, timeout=0):
        """Stops all the positioners.

        Parameters
        ----------
        positioners : list
            The list of positioners to abort. If `None`, abort all positioners.
        clear_flags : bool
            If `True`, in addition to sending ``TRAJECTORY_TRANSMISSION_ABORT``
            sends ``STOP_TRAJECTORY`` which clears all the collision and
            warning flags.
        timeout : float
            How long to wait before timing out the command. By default, just
            sends the command and does not wait for replies.

        """

        if positioners is None:
            positioners = [positioner_id for positioner_id in self.keys()
                           if not self[positioner_id].is_bootloader()]
            if positioners == []:
                return

        await self.send_to_all('TRAJECTORY_TRANSMISSION_ABORT', positioners=positioners)

        if clear_flags:
            await self.send_command('STOP_TRAJECTORY', positioner_id=0, timeout=timeout)

    async def send_trajectory(self, *args, **kwargs):
        """Sends a set of trajectories to the positioners.

        See the documentation for `.send_trajectory`.

        """

        try:
            return await send_trajectory(self, *args, **kwargs)
        except TrajectoryError as ee:
            log.error(f'sending trajectory failed with error: {ee}')
            return False

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

        bootloader = all([positioner.is_bootloader() is True
                          for positioner in self.values()])

        if not bootloader:
            log.info('stopping positioners')
            await self.stop_trajectory()

        log.info('stopping all pollers.')
        await self.pollers.stop()

        await asyncio.sleep(1)

        log.info('cancelling all pending tasks and shutting down.')

        tasks = [task for task in asyncio.all_tasks(loop=self.loop)
                 if task is not asyncio.current_task(loop=self.loop)]
        list(map(lambda task: task.cancel(), tasks))

        await asyncio.gather(*tasks, return_exceptions=True)

        self.loop.stop()

    async def __aenter__(self):
        await self.initialise()
        return self

    async def __aexit__(self, *excinfo):
        await self.shutdown()
