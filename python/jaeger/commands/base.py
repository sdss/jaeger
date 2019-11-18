#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import binascii
import logging

import can

import jaeger.utils
from jaeger import can_log, config, log
from jaeger.core import exceptions
from jaeger.maskbits import CommandStatus, ResponseCode
from jaeger.utils import AsyncQueue, StatusMixIn
from . import CommandID


__ALL__ = ['Message', 'Command']


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
                 response_code=0, extended_id=True):

        self.command = command
        self.positioner_id = positioner_id
        self.uid = uid

        uid_bits = config['positioner']['uid_bits']
        max_uid = 2**uid_bits - 1
        assert self.uid < max_uid, f'UID must be <= {max_uid}.'

        if extended_id:
            arbitration_id = jaeger.utils.get_identifier(positioner_id,
                                                         int(command.command_id),
                                                         uid=self.uid,
                                                         response_code=response_code)
        else:
            arbitration_id = positioner_id

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

        #: The UID of the message this reply is for.
        self.uid = None

        self.positioner_id, reply_cmd_id, self.uid, self.response_code = \
            jaeger.utils.parse_identifier(message.arbitration_id)

        if command is not None:
            assert command.command_id == reply_cmd_id, \
                (f'command command_id={command.command_id} and '
                 f'reply command_id={reply_cmd_id} do not match')

        self.command_id = CommandID(reply_cmd_id)

        # Does not issue a warning if at the time of queuing the command there
        # is already a command for the same positioner id running.
        self._silent_on_conflict = False

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
    loop : event loop
        The running event loop, or uses `~asyncio.get_event_loop`.
    timeout : float
        Time after which the command will be marked done. Note that if the
        command is not a broadcast and it receives replies to each one of the
        messages it sends, the command will be marked done and the timer
        cancelled. If negative, the command runs forever or until all the
        replies have been received.
    done_callback : function
        A function to call when the command has been successfully completed.
    n_positioners : int
        If the command is a broadcast, the number of positioners that should
        reply. If defined, the command will be done once as many positioners
        have replied. Otherwise it waits for the command to time out.
    data : list
        The data to pass to the messages. It must be a list in which each
        element is the payload for a message. As many messages as data elements
        will be sent. If `None`, a single message without payload will be sent.

    """

    #: The id of the command.
    command_id = None
    #: Whether the command can be broadcast to all robots.
    broadcastable = None
    #: The default timeout for this command.
    timeout = 5
    #: Whether it's safe to execute this command when the FPS is locked.
    safe = False

    def __init__(self, positioner_id, loop=None, timeout=None,
                 done_callback=None, n_positioners=None, data=None):

        assert self.broadcastable is not None, 'broadcastable not set'
        assert self.command_id is not None, 'command_id not set'

        self.positioner_id = positioner_id
        if self.positioner_id == 0 and self.broadcastable is False:
            raise exceptions.JaegerError('this command cannot be broadcast.')

        self.loop = loop or asyncio.get_event_loop()

        #: The data payload for the messages to send.
        self.data = data or []
        if not isinstance(self.data, (list, tuple)):
            self.data = [self.data]

        #: A list of messages with the responses to this command.
        self.replies = []

        # Numbers of messages to send. If the command is not a broadcast,
        # the command will be marked done after receiving this many replies.
        self.n_messages = None

        # Stores the UIDs of the messages sent for them to be compared with
        # the replies.
        self.uids = None

        if n_positioners is not None:
            assert self.is_broadcast, 'n_positioners can only be used with a broadcast.'
        self.n_positioners = n_positioners

        self.timeout = timeout or self.timeout

        # What interface and bus this command should be sent to. Only relevant
        # for multibus interfaces. To be filled by the FPS class when queueing
        # the command.
        self._interface = None
        self._bus = None

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

        msg = f'{command_name, positioner_id}: ' + msg

        for ll in logs:
            ll.log(level, msg)

    @property
    def is_broadcast(self):
        """Returns `True` if the command is a broadcast."""

        return self.positioner_id == 0

    @property
    def name(self):
        """Returns the name of this command."""

        return CommandID(self.command_id).name

    def _check_replies(self):
        """Checks if the UIDs of the replies match the messages."""

        uids = sorted(self.uids)
        replies_uids = sorted([reply.uid for reply in self.replies])
        n_messages = self.n_messages

        if self.is_broadcast:

            if self.n_positioners is None:
                return None
            else:
                uids = sorted(uids * self.n_positioners)
                n_messages *= self.n_positioners

        if len(self.replies) < n_messages:
            return None

        if len(self.replies) > n_messages:
            self._log('command received more replies than messages. '
                      'This should not be possible.',
                      level=logging.ERROR)
            self.finish_command(CommandStatus.FAILED)
            return None

        # Compares each message-reply UID.
        if not uids == replies_uids:
            self._log('the UIDs of the messages and replies do not match.',
                      level=logging.ERROR)
            return False

        return True

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
                 'received a reply from a different positioner.')

        self.replies.append(reply)

        data_hex = binascii.hexlify(reply.data).decode()
        self._log(f'positioner {reply.positioner_id} replied with '
                  f'id={reply.message.arbitration_id}, '
                  f'code={reply.response_code.name!r}, '
                  f'data={data_hex!r}')

        if reply.response_code != ResponseCode.COMMAND_ACCEPTED:

            self._log(f'command failed with code {reply.response_code.name!r}.',
                      level=logging.ERROR)

            self.finish_command(CommandStatus.FAILED)

        # If this is not a broadcast, the message was accepted and we have as
        # many replies as messages sent, mark as done.
        else:

            reply_status = self._check_replies()
            if reply_status is True:
                self.finish_command(CommandStatus.DONE)
            elif reply_status is False:
                self.finish_command(CommandStatus.FAILED)
            else:
                pass

    def finish_command(self, status, silent=False):
        """Cancels the queue watcher and removes the running command.

        Parameters
        ----------
        status : .CommandStatus
            The status to set the command to. If `None` the command will be set
            to `~.CommandStatus.DONE` if one reply for each message has been
            received, `~.CommandStatus.FAILED` otherwise.
        silent : bool
            If `True`, issues error log messages as debug.

        """

        if self._timeout_handle:
            self._timeout_handle.cancel()

        self._status = status

        self.reply_queue.watcher.cancel()

        if not self.done():

            self.set_result(self)

            is_done = (self.status == CommandStatus.DONE or
                       (self.positioner_id == 0 and self.status == CommandStatus.TIMEDOUT))

            if is_done and self._done_callback:
                if asyncio.iscoroutinefunction(self._done_callback):
                    # I don't love this because this task is unawaited but ...
                    asyncio.create_task(self._done_callback())
                    pass
                else:
                    self._done_callback()

            if self.positioner_id != 0 and self.status == CommandStatus.TIMEDOUT:
                level = logging.WARNING if not silent else logging.DEBUG
                self._log('this command timed out and it is not a broadcast.', level=level)
            elif self.status == CommandStatus.CANCELLED:
                self._log('command has been cancelled.', logging.DEBUG)
            elif self.status.failed:
                level = logging.ERROR if not silent else logging.DEBUG
                self._log(f'command finished with status {self.status.name!r}', level=level)

            self._log(f'finished command with status {self.status.name!r}')

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
                    self.timeout, self.finish_command, CommandStatus.TIMEDOUT)
        elif self.status.is_done:
            self.finish_command(self.status)

    def _generate_messages_internal(self, data=None):
        """Generates the list of messages to send to the bus for this command.

        This method is called by `.get_messages` and can be overridden in
        subclasses. Do not override `.get_messages` directly.

        """

        data = data or self.data

        if len(data) == 0:
            data = [[]]

        messages = []

        uid_bits = config['positioner']['uid_bits']
        max_uid = 2**uid_bits - 1

        use_uids = True
        if len(messages) > max_uid:
            self._log('command has more messages than available UIDs. Not assigning UIDs.',
                      level=logging.WARNING)
            use_uids = False

        for ii, data_chunk in enumerate(data):
            messages.append(
                Message(self,
                        positioner_id=self.positioner_id,
                        uid=ii if use_uids else 0,
                        data=data_chunk))

        return messages

    def get_messages(self, data=None):
        """Returns the list of messages associated with this command.

        Unless overridden, returns a single message with the associated data.

        """

        messages = self._generate_messages_internal(data=data)

        self.n_messages = len(messages)
        self.uids = [message.uid for message in messages]

        return messages

    def get_reply_for_positioner(self, positioner_id):
        """Returns the reply for a given ``positioner_id``.

        In principle this method is only useful when the command is sent in
        broadcast mode and receives replies from multiples positioners.

        """

        for reply in self.replies:
            if reply.positioner_id == positioner_id:
                return reply

        return False

    def cancel(self, silent=False):
        """Cancels a command, stopping the reply queue watcher."""

        self.finish_command(CommandStatus.CANCELLED, silent=silent)
