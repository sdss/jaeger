#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-06
# @Filename: fps.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-10 16:42:12

import asyncio

from asyncioActor.actor import Actor

from jaeger import NAME, __version__
from .can import JaegerCAN


__ALL__ = ['FPS']


class FPS(Actor):
    """A class describing the Focal Plane System that can be used as an actor.

    Parameters
    ----------
    layout : str
        A file describing the layout of the FPS.
    profile : `str` or `None`
        The configuration profile for the CAN interface, or `None` to use the
        default.
    loop : `asyncio.SelectorEventLoop`
        The asyncio event loop. If `None`, uses `asyncio.get_event_loop` to
        get a valid loop.

    """

    def __init__(self, layout=None, profile=None, loop=None, **kwargs):

        self.bus = JaegerCAN.from_profile(profile)
        self.loop = loop if loop is not None else asyncio.get_event_loop()
        # self.state = FPSState(layout=layout)

    def start_actor(self):
        """Initialises the actor."""

        super().__init__(NAME, version=__version__)
