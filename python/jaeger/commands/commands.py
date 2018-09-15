#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-14 17:09:51

import asyncio
import uuid

import can
import jaeger.utils
from jaeger import log
from jaeger.core import exceptions
from jaeger.utils import AsyncQueueMixIn, StatusMixIn
from jaeger.utils.maskbits import CommandStatus

from . import CommandID


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

    def __init__(self, command, data=[], positioner_id=0, extended_id=True, bus=None):

        self.uuid = uuid.uuid4()
        self.command = command
        self.positioner_id = positioner_id

        if extended_id:
            arbitration_id = jaeger.utils.get_identifier(positioner_id, int(command.command_id))
        else:
            arbitration_id = positioner_id

        self.bus = bus

        can.Message.__init__(self,
                             data=data,
                             arbitration_id=arbitration_id,
                             extended_id=extended_id)


class Reply(object):
    """Parses a reply message.

    Parameters
    ----------
    message : `can.Message`
        The received message
    command : `.Command`
        The `.Command` to which this message is replying.

    Attributes
    ----------
    command_id : `.CommandID` flag
        The flag with the command id.
    data : bytearray
        The data returned.
    positioner_id : int
        The positioner sending this command.
    response_code : `~jaeger.utils.maskbits.ResponseCode` flag
        The response code associated to the reply.

    """

    def __init__(self, message, command=None):

        assert isinstance(message, can.Message), 'invalid message'

        self.command = command
        self.message = message

        self.data = message.data
        self.positioner_id, command_id, self.response_code = jaeger.utils.parse_identifier(
            message.arbitration_id)

        if command is not None:
            assert command.command_id == 0 or command.command_id == command_id, \
                (f'command command_id={command.command_id} and '
                 f'command_id={command_id} do not match')

        self.command_id = CommandID(command_id)

    def __repr__(self):
        command_name = self.command.command_id.name if self.command else 'NONE'
        return (f'<Reply (command_id={command_name!r}, positioner_id={self.positioner_id}, '
                f'response_code={self.response_code.name!r})>')


class Command(StatusMixIn, AsyncQueueMixIn):
    """A command to be sent to the CAN controller.

    Implements a base class to define CAN commands to interact with the
    positioner. Commands can be composed of single or multiple messages.
    When sending a command to the bus, the first message is written to,
    then asynchronously waits for a confirmation that the message has been
    received before sending the following message. If any of the messages
    returns an error code the command is failed.

    `.Command` subclasses from `.StatusMixIn` and `.status_cb` gets called
    when the status changes.

    Parameters
    ----------
    positioner_id : int or list
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``positioner_id=0`` to broadcast to all robots.
    bus : `~jaeger.bus.JaegerCAN`
        The bus to which to send messages.
    loop : event loop
        The running event loop, or uses `~asyncio.get_event_loop`.
    timeout : float
        Time after which the command will be marked done. If `None`, uses the
        command default value.

    Attributes
    ----------
    broadcastable : bool
        Whether the command can be broadcast to all robots.
    command_id : int
        The id of the command.
    replies : list
        A list of messages with the responses to this command.
    timeout : float
        Time after which the command will be marked done.

    """

    command_id = None
    broadcastable = None
    timeout = None

    def __init__(self, positioner_id=0, bus=None, loop=None, timeout=None,
                 **kwargs):

        assert self.broadcastable is not None, 'broadcastable not set'
        assert self.command_id is not None, 'command_id not set'

        self.positioner_id = positioner_id
        if self.positioner_id == 0 and self.broadcastable is False:
            raise exceptions.JaegerError('this command cannot be broadcast.')

        self.bus = bus
        self.loop = loop or asyncio.get_event_loop()

        self.replies = []

        self.timeout = timeout or self.timeout

        StatusMixIn.__init__(self, maskbit_flags=CommandStatus,
                             initial_status=CommandStatus.READY,
                             callback_func=self.status_callback)

        AsyncQueueMixIn.__init__(self, name='reply_queue',
                                 get_callback=self.process_reply)

    def process_reply(self, reply_message):
        """Watches the reply queue."""

        if self.status != CommandStatus.RUNNING:
            raise RuntimeError('received a reply but command is not running')

        reply = Reply(reply_message, command=self)

        self.replies.append(reply)

        log.debug(f'command {self.command_id.name} got a response from '
                  f'positioner {reply.positioner_id} with '
                  f'code {reply.response_code.name!r}')

        if reply.response_code != 0:
            self.status = CommandStatus.FAILED
            return

    def status_callback(self, cmd):
        """Callback for change status.

        When the status gets set to `.CommandStatus.RUNNING` starts a timer
        that marks the command as done after `.timeout`.

        """

        def mark_done():
            self.status = CommandStatus.DONE

        if self.timeout is None or self.status != CommandStatus.RUNNING:
            return

        self.loop.call_later(self.timeout, self.reply_queue_watcher.cancel)
        self.loop.call_later(self.timeout + 0.1, mark_done)

    def get_messages(self):
        """Returns the list of messages associated with this command."""

        raise NotImplementedError('get_message must be overriden for each command subclass.')

    def send(self, bus=None, wait_for_reply=True, force=False):
        """Sends the command.

        Writes each message to the fps in turn and waits for a response.

        Parameters
        ----------
        fps : `~jaeger.fps.FPS`
            The focal plane system instance.
        wait_for_reply : bool
            If True, after sending each message associated to the command
            waits until a response for it arrives before sending the next
            message.
        force : bool
            If the command has already been finished, sending it will fail
            unless ``force=True``.

        """

        bus = bus or self.bus
        if bus is None:
            raise RuntimeError('bus not defined.')

        if self.status.is_done:
            if force is False:
                raise exceptions.JaegerError(
                    f'command {self.command_id}: trying to send a done command.')
            else:
                log.info(
                    f'command {self.command_id}: command is done but force=True. '
                    'Making command ready again.')
                self.status = CommandStatus.READY

        bus.send_command(self)
