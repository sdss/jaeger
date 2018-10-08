#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-07 21:15:24

import asyncio
import os
import pathlib

import astropy.table

from asyncioActor.actor import Actor
from jaeger import NAME, __version__, config, log
from jaeger.can import JaegerCAN
from jaeger.commands import CommandID
from jaeger.core.exceptions import JaegerUserWarning
from jaeger.positioner import Positioner
from jaeger.utils import bytes_to_int, maskbits


try:
    from sdssdb.peewee.sdss5db import targetdb
except ImportError:
    targetdb = False


__ALL__ = ['FPS']


class FPS(Actor):
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

        self.positioners = {}

    def send_command(self, command_id, positioner_id=0, data=[], block=None,
                     **kwargs):
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
        block : `bool`
            Whether to `await` for the command to be done before returning. If
            ``block=None``, will block only if the code is being run inside
            iPython.
        kwargs : dict
            Extra arguments to be passed to the command.

        """

        command_flag = CommandID(command_id)
        CommandClass = command_flag.get_command()

        command = CommandClass(positioner_id=positioner_id,
                               bus=self.bus, loop=self.loop,
                               data=data, **kwargs)

        command.send(block=block)

        return command

    def add_positioner(self, positioner, **kwargs):
        """Adds a new positioner to the list, and checks for duplicates."""

        assert isinstance(positioner, Positioner), 'positioner must be a Positioner instance'

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

        layout = layout or self.layout or config['fps']['default_layout']

        if isinstance(layout, pathlib.Path) or os.path.exists(layout):

            log.info(f'reading layout from file {layout!s}')

            data = astropy.table.Table.read(layout, format='ascii.no_header',
                                            names=['row', 'pos', 'x', 'y', 'type'])

            pos_id = 1
            for row in data:
                if row['type'].lower() == 'fiducial':
                    continue
                new_positioner = Positioner(pos_id, position=(row['x'], row['y']))
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
                self.add_positioner(Positioner(pos.id, self, position=(pos.xcen, pos.ycen)))

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

            firmware_reply = get_firmware_command.get_reply_for_positioner(positioner_id)

            if firmware_reply is None:
                log.warning(f'did not receive a reply for '
                            f'{get_firmware_command.command_id.name} for '
                            f'{positioner_id}. Skipping positioner.', JaegerUserWarning)
                continue

            positioner.firmware = '.'.join(format(byt, '02d') for byt in firmware_reply.data[1:])

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
                    log.warning(f'positioner {positioner_id} responded to '
                                f'{command_name} with response code '
                                f'{response_code.name!r}',
                                JaegerUserWarning)
            else:
                log.warning(f'{command_name} reported '
                            f'positioner_id={positioner_id} '
                            f'which was not in the layout. Skipping it.',
                            JaegerUserWarning)
                continue

            found_positioners.append(positioner_id)

        n_unknown = len(self.positioners) - len(found_positioners)
        if n_unknown > 0:
            log.warning(f'{n_unknown} positioners did not respond to '
                        f'{command_name!r}', JaegerUserWarning)

    def start_actor(self):
        """Initialises the actor."""

        super().__init__(NAME, version=__version__)
