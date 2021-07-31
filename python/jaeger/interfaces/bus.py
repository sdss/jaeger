#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-07-12
# @Filename: bus.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import abc

from .message import Message


class BusABC(object, metaclass=abc.ABCMeta):
    """A base CAN bus."""

    def __init__(self, *args, **kwargs):
        pass

    async def open(self, *args, **kwargs) -> bool:
        """Starts the bus.

        This method call the ``_open_internal`` method in the subclass bus, if
        present. It's meant mainly to initialise any process that needs to be
        run as a coroutine. Must return `True` if the connection was successful,
        `False` or an error otherwise.

        """

        return await self._open_internal(*args, **kwargs)

    async def _open_internal(self):
        return True

    @abc.abstractmethod
    async def get(self):
        """Receives messages from the bus."""

        pass

    @abc.abstractmethod
    def send(self, msg: Message, **kwargs):
        """Sends a message to the bus."""

        pass
