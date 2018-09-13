#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-12 20:00:56

import abc
import asyncio
import uuid

import can
import jaeger.utils
from jaeger import log
from jaeger.core import exceptions
from jaeger.state import StatusMixIn
from jaeger.utils.maskbits import CommandStatus


__ALL__ = ['Message', 'Command']


class Message(can.Message):
    """An extended `can.Message` class.

    Expands the `can.Message` class to handle custom arbitration IDs for
    extended frames..

    Parameters
    ----------
    command : `.Command`
        The command associated with this message.
    data : list or bytearray
        Payload to pass to `can.Message`.
    positioner_id : int
        The positioner to which the message will be sent (0 for broadcast).
    extended_id : bool
        Whether the id is an 11 bit (False) or 29 bit (True) address.

    Attributes
    ----------
    uuid : `uuid.uuid4`
        A unique identifier for the message based on `uuid.uuid4`.

    """

    def __init__(self, command, data, positioner_id=0, extended_id=True):

        self.uuid = uuid.uuid4()
        self.command = command
        self.positioner_id = positioner_id

        if extended_id:
            arbitration_id = jaeger.utils.get_identifier(positioner_id, command.command_id)
        else:
            arbitration_id = positioner_id

        can.Message.__init__(self,
                             data=data,
                             arbitration_id=arbitration_id,
                             extended_id=extended_id)


class Command(abc.ABCMeta, StatusMixIn):
    """A command to be sent to the CAN controller.

    Implements a base class to define CAN commands to interact with the
    positioner. Commands can be composed of single or multiple messages.
    When sending a command to the bus, the first message is written to,
    then asynchronously waits for a confirmation that the message has been
    received before sending the following message. If any of the messages
    returns an error code the command is failed.

    Parameters
    ----------
    positioner_id : int or list
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``positioner_id=0`` to broadcast to all robots.
    callback_func : function
        The callback function to call when the status changes.

    Attributes
    ----------
    broadcastable : bool
        Whether the command can be broadcast to all robots.
    command_id : int
        The id of the command.
    reply : `.Reply`
        A `.Reply` object representing the responses to this command.

    """

    command_id = None
    broadcastable = None

    def __init__(self, positioner_id=0):

        assert self.broadcastable is not None, 'broadcastable not set'
        assert self.command_id is not None, 'command_id not set'

        self.positioner_id = positioner_id
        if self.positioner_id == 0 and self.broadcastable is False:
            raise exceptions.JaegerError('this command cannot be broadcast.')

        self._reply_queue = asyncio.Queue()
        self.replies = []

        StatusMixIn.__init__(self, maskbit_flags=CommandStatus,
                             initial_status=CommandStatus.READY)

    @abc.abstractmethod
    def get_messages(self):
        """Returns the list of messages associated with this command."""

        pass

    async def _send_coro(self, bus, wait_for_reply):
        """Async coroutine to send messages to the bus."""

        for message in self.get_messages():

            bus.send(message)
            log.debug(f'sent message {message.uuid!s} with '
                      f'arbitration_id={message.arbitration_id} '
                      f'and payload {message.data!r}')
            try:
                reply = await asyncio.wait_for(self._reply_queue.get, 5)
            except asyncio.TimeoutError:
                self.status = CommandStatus.CANCELLED
                log.warning(f'failed receiving reply for message '
                            f'{message.uuid!s}', exceptions.JaegerUserWarning)
                return

            assert isinstance(reply, can.Message), 'invalid reply type'

            positioner_id, command_id, reply_flag = \
                jaeger.utils.parse_identifier(reply.arbitration_id)

    def send(self, bus, wait_for_reply=True, force=False):
        """Sends the command.

        Writes each message to the bus in turn and waits for a response.

        Parameters
        ----------
        bus : `~jaeger.can.JaegerCAN`
            The CAN interface bus.
        wait_for_reply : bool
            If True, after sending each message associated to the command
            waits until a response for it arrives before sending the next
            message.
        force : bool
            If the command has already been finished, sending it will fail
            unless ``force=True``.

        """

        if self.status.is_done:
            if force is False:
                raise exceptions.JaegerError(
                    f'command {self.command_id}: trying to send a done command.')
            else:
                log.info(
                    f'command {self.command_id}: command is done but force=True. '
                    'Making command ready again.')
                self.status = CommandStatus.READY

        self.status = CommandStatus.RUNNING

        self.loop.create_task(self._send_coro(), bus, wait_for_reply)
