#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-02 22:05:37

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


class Command(StatusMixIn, AsyncQueueMixIn, asyncio.Future):
    """A command to be sent to the CAN controller.

    Implements a base class to define CAN commands to interact with the
    positioner. Commands can be composed of single or multiple messages.
    When sending a command to the bus, the first message is written to,
    then asynchronously waits for a confirmation that the message has been
    received before sending the following message. If any of the messages
    returns an error code the command is failed.

    `.Command` subclasses from `.StatusMixIn` and `.status_callback` gets
    called when the status changes.

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

    """

    #: The id of the command.
    command_id = None
    #: Whether the command can be broadcast to all robots.
    broadcastable = None
    #: Time after which the command will be marked done.
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

        #: A list of messages with the responses to this command.
        self.replies = []

        self.timeout = timeout or self.timeout

        self._data = kwargs.pop('data', [])

        StatusMixIn.__init__(self, maskbit_flags=CommandStatus,
                             initial_status=CommandStatus.READY,
                             callback_func=self.status_callback)

        AsyncQueueMixIn.__init__(self, name='reply_queue',
                                 get_callback=self.process_reply,
                                 loop=self.loop)

        asyncio.Future.__init__(self, loop=self.loop)
        self.add_done_callback(self.finish_command)

    def __repr__(self):
        return (f'<Command {self.command_id.name} '
                f'(positioner_id={self.positioner_id}, '
                f'status={self.status.name!r})>')

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

    def finish_command(self, status=CommandID.DONE):
        """Cancels the queue watcher and removes the running command."""

        if self.done():
            return

        self.status = status
        self.set_result(status)

        self.reply_queue_watcher.cancel()

        if self.bus is not None:
            r_command = self.bus.is_command_running(self.positioner_id, self.command_id)
            if r_command:
                self.bus.running_commands[r_command.positioner_id].pop(r_command.command_id)

    def status_callback(self, cmd):
        """Callback for change status.

        When the status gets set to `.CommandStatus.RUNNING` starts a timer
        that marks the command as done after `.timeout`.

        """

        log.debug(f'command {self.command_id.name} changed status to {self.status.name}')

        if self.status == CommandStatus.RUNNING and self.timeout is not None:
            self.loop.call_later(self.timeout, self.finish_command, CommandStatus.DONE)
        elif self.status.is_done:
            self.finish_command(self.status)
        else:
            return

    def get_messages(self):
        """Returns the list of messages associated with this command.

        Unless overridden, returns a single message with the associated data.

        """

        return [Message(self, positioner_id=self.positioner_id, data=self._data)]

    def send(self, bus=None, wait_for_reply=True, force=False, block=None):
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
        block : `bool`
            Whether to `await` for the command to be done before returning. If
            ``block=None``, will block only if the code is being run inside
            iPython.

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

        coro = bus.send_command(self, block=block)
        if not self.loop.is_running():
            self.loop.run_until_complete(coro)
        else:
            self.loop.create_task(coro)

    def get_reply_for_positioner(self, positioner_id):
        """Returns the reply for a given ``positioner_id``.

        In principle this method is only useful when the command is sent in
        broadcast mode and receives replies from multiples positioners.

        """

        for reply in self.replies:
            if reply.positioner_id == positioner_id:
                return reply

        return False


class Abort(Command):
    """Cancels any running command. Stops the positioner if it is moving."""

    command_id = CommandID.ABORT
    broadcastable = True
    timeout = 5
