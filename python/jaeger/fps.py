#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-02 19:02:43

import asyncio

import astropy.table

from asyncioActor.actor import Actor
from jaeger import NAME, __version__, log
from jaeger.can import JaegerCAN
from jaeger.commands import CommandID
from jaeger.core.exceptions import JaegerUserWarning
from jaeger.utils import StatusMixIn
from jaeger.utils.maskbits import CommandStatus, PositionerStatus


__ALL__ = ['FPS', 'Positioner']


class Positioner(StatusMixIn):
    r"""Represents the status and parameters of a positioner.

    Parameters
    ----------
    positioner_id : int
        The ID of the positioner
    position : tuple
        The :math:`(x_{\rm focal}, y_{\rm focal})` coordinates of the
        central axis of the positioner.
    alpha : float
        Position of the alpha arm, in degrees.
    beta : float
        Position of the beta arm, in degrees.

    """

    def __init__(self, positioner_id, position=None, alpha=None, beta=None):

        self.positioner_id = positioner_id
        self.position = position
        self.alpha = alpha
        self.beta = beta
        self.firmware = None

        super().__init__(maskbit_flags=PositionerStatus,
                         initial_status=PositionerStatus.UNKNOWN)

    def reset(self):
        """Resets positioner values and statuses."""

        self.position = None
        self.alpha = None
        self.beta = None
        self.status = PositionerStatus.UNKNOWN
        self.firmware = None

    def __repr__(self):
        return f'<Positioner (id={self.positioner_id}, status={self.status.name!r})>'


class FPS(Actor, asyncio.Future):
    """A class describing the Focal Plane System that can be used as an actor.

    `.FPS` is a `asyncio.Future` that becomes completed when the initialisation
    finishes. The initialisation can be awaited ::

        >>> fps = FPS(layout='my_layout.dat')
        >>> await fps
        >>> print(fps.positioners)

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

    """

    def __init__(self, layout=None, can_profile=None, loop=None, **kwargs):

        self.loop = loop if loop is not None else asyncio.get_event_loop()
        self.bus = JaegerCAN.from_profile(can_profile, loop=loop)

        self.positioners = {}

        asyncio.Future.__init__(self, loop=self.loop)

        coro = self.load_positioners(layout)
        self.loop.create_task(coro)

    def send_command(self, command_id, positioner_id=0, data=[], block=None):
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

        """

        command_flag = CommandID(command_id)
        CommandClass = command_flag.get_command()

        command = CommandClass(positioner_id=positioner_id,
                               bus=self.bus, loop=self.loop,
                               data=data)

        command.send(block=block)

        return command

    def add_positioner(self, positioner, **kwargs):
        """Adds a new positioner to the list, and checks for duplicates."""

        assert isinstance(positioner, Positioner), 'positioner must be a Positioner instance'

        if positioner.positioner_id in self.positioners:
            raise ValueError(f'there is already a positioner in the list with '
                             f'positioner_id {positioner.positioner_id}.')

        self.positioners[positioner.positioner_id] = positioner

    async def load_positioners(self, layout=None, check_positioners=True):
        """Loads positioner information from a layout file or from CAN.

        Parameters
        ----------
        layout : `str` or `pathlib.Path`
            Path to a layout file. If `None`, the information for the currently
            connected positioner will be retrieved from calls to the bus.
        check_positioners : bool
            If ``True`` and ``layout`` is a file, the CAN interface will be
            used to confirm that each positioner is connected and to fill out
            additional information such as ``alpha`` and ``beta``.

        """

        if layout is not None:

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

            if not check_positioners:
                return

            # Resets all positioner
            for positioner in self.positioners.values():
                positioner.reset()

            get_id_command = self.send_command(CommandID.GET_ID,
                                               positioner_id=0,
                                               block=False)

            await get_id_command.wait_for_status(CommandStatus.DONE, loop=self.loop)

            # Loops over each reply and set the positioner status to OK. If the
            # positioner was not in the list, adds it. Checks how many positioner
            # did not reply.
            found_positioners = []
            for reply in get_id_command.replies:

                positioner_id = reply.positioner_id
                found_positioners.append(positioner_id)

                if positioner_id in self.positioners:
                    if reply.response_code == reply.response_code.COMMAND_ACCEPTED:
                        log.debug(f'positioner {positioner_id} status set to '
                                  f'{PositionerStatus.OK.name!r}')
                        self.positioners[positioner_id].status = PositionerStatus.OK
                    else:
                        log.warning(f'positioner {positioner_id} responded to '
                                    f'{get_id_command.command_id} with response code '
                                    f'{reply.response_code.name!r}', JaegerUserWarning)
                        log.debug(f'positioner {positioner_id} status set to '
                                  f'{PositionerStatus.UNKNOWN.name!r}')
                        self.positioners[positioner_id].status = PositionerStatus.UNKNOWN
                else:
                    log.warning(f'{get_id_command.command_id} reported '
                                f'positioner_id={positioner_id} which was '
                                f'not in the list. Adding it.', JaegerUserWarning)
                    self.positioners[positioner_id] = Positioner(positioner_id)
                    self.positioners[positioner_id].status = PositionerStatus.OK

            n_unknown = len(self.positioners) - len(found_positioners)
            if n_unknown > 0:
                log.warning(f'{n_unknown} positioners did not respond to '
                            f'{get_id_command.command_id.name!r}', JaegerUserWarning)

        log.debug('retrieving firmware version')
        get_firmaware_command = self.send_command(CommandID.GET_FIRMWARE_VERSION,
                                                  positioner_id=0,
                                                  block=False)
        await get_firmaware_command.wait_for_status(CommandStatus.DONE, loop=self.loop)

        for reply in get_firmaware_command.replies:
            firmware = '.'.join(str(byt) for byt in reply.data[1:])
            self.positioners[reply.positioner_id].firmware = firmware

        self.set_result('initialisation done')

    def start_actor(self):
        """Initialises the actor."""

        super().__init__(NAME, version=__version__)
