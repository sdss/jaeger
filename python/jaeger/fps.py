#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-13 22:00:44

import asyncio

import astropy

from asyncioActor.actor import Actor
from jaeger import NAME, __version__
from jaeger.utils.maskbits import PositionerStatus

from .can import JaegerCAN
from .state import StatusMixIn


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
        self.position = None
        self.alpha = alpha
        self.beta = beta

        super().__init__(maskbit_flags=PositionerStatus,
                         initial_status=PositionerStatus.UNKNOWN,
                         callback_func=self._status_change_cb)

    def __repr__(self):
        return f'<Positioner (id={self.positioner_id}, status={self.status.name!r})>'

    def _status_change_cb(self):
        pass


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
    loop : `asyncio.SelectorEventLoop`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.

    """

    def __init__(self, layout=None, can_profile=None, loop=None, **kwargs):

        self.bus = JaegerCAN.from_profile(can_profile)
        self.loop = loop if loop is not None else asyncio.get_event_loop()

        self.positioners = {}
        self.load_positioners(layout)

    def add_positioner(self, positioner, **kwargs):
        """Adds a new positioner to the list, and checks for duplicates."""

        assert isinstance(positioner, Positioner), 'positioner must be a Positioner instance'

        if positioner.positioner_id in self.positioners:
            raise ValueError(f'there is already a positioner in the list with '
                             f'positioner_id {positioner.positioner_id}.')

        self.positioners[positioner.positioner_id] = positioner

    def load_positioners(self, layout=None, check_positioners=True):
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

    def start_actor(self):
        """Initialises the actor."""

        super().__init__(NAME, version=__version__)
