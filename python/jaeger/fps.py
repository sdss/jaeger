#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-10 18:44:29

import asyncio
import os
import pathlib
from contextlib import suppress

import astropy.table
from ruamel.yaml import YAML

from jaeger import config, log, maskbits
from jaeger.can import JaegerCAN
from jaeger.commands import CommandID
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
    layout : str
        A file describing the layout of the FPS. If `None`, the CAN interface
        will be use to determine the positioners connected.
    can_profile : `str` or `None`
        The configuration profile for the CAN interface, or `None` to use the
        default one.
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

    def __init__(self, layout=None, can_profile=None, loop=None, **kwargs):

        self.loop = loop if loop is not None else asyncio.get_event_loop()
        self.bus = JaegerCAN.from_profile(can_profile, loop=loop)
        self.layout = layout

        #: A list of `~jaeger.positioner.Positioner` instances associated with
        #: this `.FPS` instance.
        self.positioners = {}

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

        command = CommandClass(positioner_id=positioner_id,
                               bus=self.bus, loop=self.loop,
                               data=data, **kwargs)

        if not command.send():
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
        """Loads positioner information from a layout file or from CAN.

        Parameters
        ----------
        layout : `str` or `pathlib.Path`
            Either the path to a layout file or a string with the layout name
            to be retrieved from the database. If ``layout=None``, retrieves
            the default layout as defined in the config from the DB.
        check_positioners : bool
            If ``True`` and ``layout`` is a file, the CAN interface will be
            used to confirm that each positioner is connected and to fill out
            additional information such as ``alpha`` and ``beta``.

        """

        log.info('starting FPS initialisation')

        layout = layout or self.layout or config['fps']['default_layout']

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

            log.debug(f'loaded positions for {pos_id-1} positioners')

        else:

            log.info(f'reading profile {layout} from database')

            if not targetdb.database.connected:
                targetdb.database.connect()
            assert targetdb.database.connected, 'database is not connected.'

            positioners_db = targetdb.Actuator.select().join(
                targetdb.FPSLayout).switch(targetdb.Actuator).join(
                    targetdb.ActuatorType).filter(
                        targetdb.FPSLayout.label == 'central_park',
                        targetdb.ActuatorType.label == 'Robot')

            for pos in positioners_db:
                self.add_positioner(Positioner(pos.id, self,
                                               centre=(pos.xcen, pos.ycen)))

            log.debug(f'loaded positions for {positioners_db.count()} positioners')

        if not check_positioners:
            return

        # Resets all positioner
        for positioner in self.positioners.values():
            positioner.reset()

        get_status_command = self.send_command(CommandID.GET_STATUS,
                                               positioner_id=0,
                                               timeout=2,
                                               block=False)
        get_firmware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                 positioner_id=0,
                                                 timeout=2,
                                                 block=False)

        await asyncio.gather(get_status_command, get_firmware_command)

        # Loops over each reply and set the positioner status to OK. If the
        # positioner was not in the list, adds it. Checks how many positioner
        # did not reply.
        found_positioners = []
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

            found_positioners.append(positioner_id)

        n_unknown = len(self.positioners) - len(found_positioners)
        if n_unknown > 0:
            log.warning(f'{n_unknown} positioners did not respond to '
                        f'{status_reply.command_id.name!r}', JaegerUserWarning)

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

    async def send_trajectory(self, trajectories, kaiju_check=True):
        """Sends a set of trajectories to the positioners.

        .. danger:: This method can cause the positioners to collide if it is
            commanded with ``kaiju_check=False``.

        Parameters
        ----------
        trajectories : `str` or `dict`
            Either a path to a YAML file to read or a dictionary with the
            trajectories. In either case the format must be a dictionary in
            which the keys are the ``positioner_ids`` and each value is a
            dictionary containing two keys: ``alpha`` and ``beta``, each
            pointing to a list of tuples ``(position, time)``, where
            ``position`` is in degrees and ``time`` is in seconds.
        kaiju_check : `bool`
            Whether to check the trajectories with kaiju before sending it.

        Examples
        --------
        ::

            >>> fps = FPS()
            >>> await fps.initialise()

            # Send a trajectory with two points for positioner 4
            >>> await fps.send_trajectory({1: 'alpha': [(90, 0), (91, 3)],
                                              'beta': [(20, 0), (23, 4)]})

        """

        PosStatus = maskbits.PositionerStatus

        if isinstance(trajectories, (str, pathlib.Path)):
            yaml = YAML(typ='safe')
            trajectories = yaml.load(open(trajectories))
        elif isinstance(trajectories, dict):
            pass
        else:
            raise ValueError('invalid trajectory data.')

        if kaiju_check:
            # TODO: implement call to kaiju
            pass
        else:
            log.warning('about to send a trajectory that has not been checked '
                        'by kaiju. This will end up in tears.',
                        JaegerUserWarning)

        await self.update_status(positioners=list(trajectories.keys()),
                                 timeout=1.)

        # TODO: better deal with broken/unknown status positioners.

        n_points = {}
        max_time = 0.0

        # Check that all positioners are ready to receive a new trajectory.
        for pos_id in trajectories:

            positioner = self.positioners[pos_id]
            status = positioner.status

            if (PosStatus.DATUM_INITIALIZED not in status or
                    PosStatus.DISPLACEMENT_COMPLETED not in status):
                log.error(f'positioner_id={pos_id} is not '
                          f'ready to receive a trajectory.')
                return False

            traj_pos = trajectories[pos_id]

            n_points[pos_id] = (len(traj_pos['alpha']), len(traj_pos['beta']))

            # Gets maximum time for this positioner
            max_time_pos = max([max(list(zip(*traj_pos['alpha']))[1]),
                                max(list(zip(*traj_pos['beta']))[1])])

            # Updates the global trajectory max time.
            if max_time_pos > max_time:
                max_time = max_time_pos

        # Starts trajectory
        new_traj_cmds = [self.send_command('SEND_NEW_TRAJECTORY',
                                           positioner_id=pos_id,
                                           n_alpha=n_points[pos_id][0],
                                           n_beta=n_points[pos_id][1])
                         for pos_id in n_points]

        await asyncio.gather(*new_traj_cmds)

        # Send trajectory points
        traj_data_cmds = []

        for pos_id in trajectories:

            alpha = trajectories[pos_id]['alpha']
            beta = trajectories[pos_id]['beta']

            traj_data_cmds.append(self.send_command('SEND_TRAJECTORY_DATA',
                                                    positioner_id=pos_id,
                                                    alpha=alpha, beta=beta))

        await asyncio.gather(*traj_data_cmds)

        # Finalise the trajectories
        end_traj_cmds = [self.send_command('TRAJECTORY_DATA_END',
                                           positioner_id=pos_id)
                         for pos_id in trajectories]

        await asyncio.gather(*end_traj_cmds)

        for cmd in end_traj_cmds:

            if len(cmd.replies) == 0:
                log.error(f'positioner_id={cmd.positioner_id} did not get '
                          f'a reply to {cmd.command_id.name!r}. '
                          'Aborting trajectory.')
                self._abort_trajectory(trajectories.keys())
                return False

            if maskbits.ResponseCode.INVALID_TRAJECTORY in cmd.replies[0].response_code:
                log.error(f'positioner_id={cmd.positioner_id} got an '
                          f'INVALID_TRAJECTORY reply. Aborting trajectory.')
                self._abort_trajectory(trajectories.keys())
                return False

        # Prepare to start the trajectories. Make position polling faster and
        # output expected time.
        log.info(f'expected time to complete trajectory: '
                 f'{max_time:.2f} seconds.')

        for pos_id in trajectories:
            self.positioners[pos_id].position_poller.set_delay(0.5)

        # Start trajectories
        await self.send_command('START_TRAJECTORY', positioner_id=0, timeout=1)

        # Wait approximate time before starting to poll for status
        await asyncio.sleep(0.95 * max_time, loop=self.loop)

        # Wait until all positioners have completed.
        wait_status = [self.positioners[pos_id].wait_for_status(
            PosStatus.DISPLACEMENT_COMPLETED, timeout=max_time + 3)
            for pos_id in trajectories]
        results = await asyncio.gather(*wait_status, loop=self.loop)

        if not all(results):
            log.error('some positioners did not complete the move.')
            return False

        log.info('all positioners have reached their final positions.')

        # Restore default polling time
        for pos_id in trajectories:
            self.positioners[pos_id].position_poller.set_delay()

        return True

    async def shutdown(self):

        log.info('cancelling all pending tasks and shutting down.')

        pending = asyncio.Task.all_tasks()

        for task in pending:
            task.cancel()
            # Now we should await task to execute it's cancellation.
            # Cancelled task raises asyncio.CancelledError that we suppress.
            with suppress(asyncio.CancelledError):
                await task

    def start_actor(self):
        """Initialises the actor."""

        raise NotImplementedError('the actor functionality '
                                  'has not yet been implemented.')
