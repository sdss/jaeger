#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-04-11 16:25:31

import asyncio
import logging

import can

import jaeger.utils
from jaeger import can_log, config, log
from jaeger.core import exceptions
from jaeger.maskbits import CommandStatus, ResponseCode
from jaeger.utils import AsyncQueue, StatusMixIn
from . import CommandID


__ALL__ = ['Message', 'Command', 'Abort']


class Message(can.Message):
    """An extended `can.Message` class.

    Expands the `can.Message` class to handle custom arbitration IDs for
    extended frames.

    Parameters
    ----------
    command : `.Command`
        The command associated with this message.
    data : list or bytearray
        Payload to pass to `can.Message`.
    positioner_id : int
        The positioner to which the message will be sent (0 for broadcast).
    uid : int
        The unique identifier for this message.
    extended_id : bool
        Whether the id is an 11 bit (False) or 29 bit (True) address.

    """

    def __init__(self, command, data=[], positioner_id=0, uid=0,
                 extended_id=True, bus=None):

        self.command = command
        self.positioner_id = positioner_id
        self.uid = uid

        uid_bits = config['uid_bits']
        max_uid = 2**uid_bits - 1
        assert self.uid < max_uid, f'UID must be <= {max_uid}.'

        if extended_id:
            arbitration_id = jaeger.utils.get_identifier(positioner_id,
                                                         int(command.command_id))
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
    response_code : `~jaeger.maskbits.ResponseCode` flag
        The response code associated to the reply.

    """

    def __init__(self, message, command=None):

        assert isinstance(message, can.Message), 'invalid message'

        #: The command for which this reply is intended.
        self.command = command

        #: The raw `~can.Message`.
        self.message = message

        #: The data from the message.
        self.data = message.data

        #: The `~.maskbits.ResponseCode` bit returned by the reply.
        self.response_code = None

        self.positioner_id, reply_cmd_id, __, self.response_code = \
            jaeger.utils.parse_identifier(message.arbitration_id)

        if command is not None:
            assert command.command_id == reply_cmd_id, \
                (f'command command_id={command.command_id} and '
                 f'reply command_id={reply_cmd_id} do not match')

        self.command_id = CommandID(reply_cmd_id)

    def __repr__(self):
        command_name = self.command.command_id.name if self.command else 'NONE'
        return (f'<Reply (command_id={command_name!r}, '
                f'positioner_id={self.positioner_id}, '
                f'response_code={self.response_code.name!r})>')


class Command(StatusMixIn, asyncio.Future):
    """A command to be sent to the CAN controller.

    Implements a base class to define CAN commands to interact with the
    positioner. Commands can be composed of single or multiple messages.
    When sending a command to the bus, the first message is written to,
    then asynchronously waits for a confirmation that the message has been
    received before sending the following message. If any of the messages
    returns an error code the command is failed.

    `.Command` subclasses from `.StatusMixIn` and `.status_callback` gets
    called when the status changes.

    `.Command` is a `~asyncio.Future` and must be awaited. The
    `~asyncio.Future` is done when `~.Command.finish_command` is called,
    which happens when the status is marked done or cancelled or when the
    command timeouts.

    Commands sent to a single positioner are marked done when a reply is
    received from the same positioner for the given command, or when it
    times out. Broadcast commands only get marked done by timing out or
    manually.

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
        Time after which the command will be marked done. Note that if the
        command is not a broadcast and it receives replies to each one of the
        messages it sends, the command will be marked done and the timer
        cancelled. If `None`, the command runs forever or until replies are
        received.
    done_callback : function
        A function to call when the command has been successfully completed.

    """

    #: The id of the command.
    command_id = None
    #: Whether the command can be broadcast to all robots.
    broadcastable = None

    def __init__(self, positioner_id, bus=None, loop=None, timeout=5.,
                 done_callback=None, **kwargs):

        assert self.broadcastable is not None, 'broadcastable not set'
        assert self.command_id is not None, 'command_id not set'

        self.positioner_id = positioner_id
        if self.positioner_id == 0 and self.broadcastable is False:
            raise exceptions.JaegerError('this command cannot be broadcast.')

        self.bus = bus
        self.loop = loop or asyncio.get_event_loop()

        #: A list of messages with the responses to this command.
        self.replies = []

        # Numbers of messages to send. If the command is not a broadcast,
        # the command will be marked done after receiving this many replies.
        self._n_messages = 1

        self.timeout = timeout

        self._data = kwargs.pop('data', [])
        self._done_callback = done_callback

        self._override = False

        self._timeout_handle = None

        self.reply_queue = AsyncQueue(callback=self.process_reply,
                                      loop=self.loop)

        StatusMixIn.__init__(self, maskbit_flags=CommandStatus,
                             initial_status=CommandStatus.READY,
                             callback_func=self.status_callback)

        asyncio.Future.__init__(self, loop=self.loop)

    def __repr__(self):
        return (f'<Command {self.command_id.name} '
                f'(positioner_id={self.positioner_id}, '
                f'status={self.status.name!r})>')

    def _log(self, msg, level=logging.DEBUG, command_id=None,
             positioner_id=None, logs=[can_log]):
        """Logs a message."""

        command_id = command_id or self.command_id
        command_name = command_id.name

        positioner_id = positioner_id or self.positioner_id

        msg = f'{command_name, self.positioner_id}: ' + msg

        for ll in logs:
            ll.log(level, msg)

    def process_reply(self, reply_message):
        """Watches the reply queue."""

        command_name = self.command_id.name

        if self.status != CommandStatus.RUNNING:
            log.error(f'{command_name, self.positioner_id}: '
                      'received a reply but command is not running')
            return

        reply = Reply(reply_message, command=self)

        if self.positioner_id != 0:
            assert reply.positioner_id == self.positioner_id, \
                (f'({command_name, self.positioner_id}): '
                 'received a reply from an invalid positioner.')

        self.replies.append(reply)

        self._log(f'positioner replied code={reply.response_code.name!r} '
                  f'data={reply.data}', positioner_id=reply.positioner_id)

        if reply.response_code != ResponseCode.COMMAND_ACCEPTED:

            self.finish_command(CommandStatus.FAILED)

            self._log(f'command failed with code {reply.response_code.name}.',
                      level=logging.ERROR, logs=[can_log, log])

        # If this is not a broadcast, the message was accepted and we have as
        # many replies as messages sent, mark as done.
        elif (reply.response_code == ResponseCode.COMMAND_ACCEPTED and
                self.positioner_id != 0 and
                len(self.replies) == self._n_messages):

            self.status = CommandStatus.DONE

    def finish_command(self, status=None, timed_out=False):
        """Cancels the queue watcher and removes the running command.

        Parameters
        ----------
        status : `.CommandStatus` or `None`
            The status to set the command to. If `None` the command will be set
            to `~.CommandStatus.DONE` if one reply for each message has been
            received, `~.CommandStatus.FAILED` otherwise.
        timed_out : `bool`
            Whether the command if being finished because it timed out.

        """

        if self._timeout_handle:
            self._timeout_handle.cancel()

        if not self.status.is_done:

            if timed_out:
                self._log('command timed out. Finishing it.')

            if status:
                self._status = status
            else:
                n_replies = len(self.replies)
                if ((self.positioner_id != 0 and n_replies == self._n_messages) or
                        (self.positioner_id == 0 and n_replies >= 1)):
                    self._status = CommandStatus.DONE
                else:
                    self._status = CommandStatus.FAILED

        self.reply_queue.watcher.cancel()

        if self.bus is not None:
            r_command = self.bus.is_command_running(self.positioner_id, self.command_id)
            if r_command:
                self.bus.running_commands[r_command.positioner_id].pop(r_command.command_id)

        if not self.done():
            self.set_result(self)

            if self.status == CommandStatus.DONE and self._done_callback:
                self._done_callback()

    def status_callback(self):
        """Callback for change status.

        When the status gets set to `.CommandStatus.RUNNING` starts a timer
        that marks the command as done after `.timeout`.

        """

        self._log(f'status changed to {self.status.name}')

        if self.status == CommandStatus.RUNNING:
            if self.timeout is None or self.timeout < 0:
                pass
            elif self.timeout == 0:
                self.finish_command(CommandStatus.DONE)
            else:
                self._timeout_handle = self.loop.call_later(
                    self.timeout, self.finish_command, None, True)
        elif self.status.is_done:
            self.finish_command()
        else:
            return

    def get_messages(self, data=None):
        """Returns the list of messages associated with this command.

        Unless overridden, returns a single message with the associated data.

        """

        data = data or self._data

        if len(self._data) == 0:
            messages = [Message(self, positioner_id=self.positioner_id, data=data)]
        else:
            messages = [Message(self, positioner_id=self.positioner_id, uid=ii,
                                data=data_chunk) for ii, data_chunk in enumerate(data)]

        self._n_messages = len(messages)

        return messages

    def send(self, bus=None, override=False):
        """Queues the command for execution.

        Adds the command to the
        `JaegerCAN.command_queue <jaeger.can.JaegerCAN.command_queue>` so that
        it can be processed.

        Parameters
        ----------
        fps : `~jaeger.fps.FPS`
            The focal plane system instance.
        override : bool
            If another instance of this command_id with the same positioner_id
            is running, cancels it and schedules this one immediately.
            Otherwise the command is queued until the first one finishes.

        Returns
        -------
        result : `bool`
            `True` if the command was sent to the bus command queue. `False` if
            and error occurred. The error is logged.

        """

        bus = bus or self.bus
        if bus is None:
            raise RuntimeError('bus not defined.')

        if self.status.is_done:
            self._log('trying to send a done command.', level=logging.ERROR,
                      logs=[can_log, log])
            return False

        self._override = override

        bus.command_queue.put_nowait(self)

        self._log('added command to CAN processing queue.')

        return True

    def get_reply_for_positioner(self, positioner_id):
        """Returns the reply for a given ``positioner_id``.

        In principle this method is only useful when the command is sent in
        broadcast mode and receives replies from multiples positioners.

        """

        for reply in self.replies:
            if reply.positioner_id == positioner_id:
                return reply

        return False
