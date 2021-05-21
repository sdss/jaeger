#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import binascii
import collections
import logging
import time

from typing import Callable, List, Optional

import can

from jaeger import can_log, config, log, maskbits
from jaeger.exceptions import CommandError, JaegerError
from jaeger.maskbits import CommandStatus, ResponseCode
from jaeger.utils import AsyncQueue, StatusMixIn, get_identifier, parse_identifier

from . import CommandID


__all__ = ["Message", "Command"]


# A pool of UIDs that can be assigned to each command for a given command_id.
# The format of the pool is UID_POOL[command_id][positioner_id] so that each
# positioner has uid_bits (64) messages for each command id. UID=0 is always
# reserved for broadcasts.
UID_POOL = collections.defaultdict(dict)

# Starting value for command UID.
COMMAND_UID = 0


class Message(can.Message):
    """An extended `can.Message` class.

    Expands the `can.Message` class to handle custom arbitration IDs for
    extended frames.

    Parameters
    ----------
    command
        The command associated with this message.
    data
        Payload to pass to `can.Message`.
    positioner_id
        The positioner to which the message will be sent (0 for broadcast).
    uid
        The unique identifier for this message.
    extended_id
        Whether the id is an 11 bit (False) or 29 bit (True) address.

    """

    def __init__(
        self,
        command: Command,
        data: bytearray = bytearray([]),
        positioner_id: int = 0,
        uid: int = 0,
        response_code: int = 0,
        extended_id: bool = True,
    ):

        self.command = command
        self.positioner_id = positioner_id
        self.uid = uid

        uid_bits = config["positioner"]["uid_bits"]
        max_uid = 2 ** uid_bits
        assert self.uid < max_uid, f"UID must be <= {max_uid}."

        if extended_id:
            arbitration_id = get_identifier(
                positioner_id,
                int(command.command_id),
                uid=self.uid,
                response_code=response_code,
            )
        else:
            arbitration_id = positioner_id

        can.Message.__init__(
            self,
            data=data,
            arbitration_id=arbitration_id,
            is_extended_id=extended_id,
        )


class Reply(object):
    """Parses a reply message.

    Parameters
    ----------
    message
        The received message
    command
        The `.Command` to which this message is replying.

    """

    def __init__(self, message: can.Message, command: Optional[Command] = None):

        assert isinstance(message, can.Message), "invalid message"

        #: The command for which this reply is intended.
        self.command = command

        #: The raw `~can.Message`.
        self.message = message

        #: The data from the message.
        self.data = message.data

        #: The `~.maskbits.ResponseCode` bit returned by the reply.
        self.response_code: maskbits.ResponseCode

        #: The UID of the message this reply is for.
        self.uid: int

        (
            self.positioner_id,
            reply_cmd_id,
            self.uid,
            self.response_code,
        ) = parse_identifier(message.arbitration_id)

        if command is not None:
            assert command.command_id == reply_cmd_id, (
                f"Command command_id={command.command_id} and "
                f"reply command_id={reply_cmd_id} do not match"
            )

        self.command_id = CommandID(reply_cmd_id)

    def __repr__(self):
        command_name = self.command.command_id.name if self.command else "NONE"
        return (
            f"<Reply (command_id={command_name!r}, "
            f"positioner_id={self.positioner_id}, "
            f"response_code={self.response_code.name!r})>"
        )


class Command(StatusMixIn[CommandStatus], asyncio.Future):
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
    positioner_id
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``positioner_id=0`` to broadcast to all robots.
    loop
        The running event loop, or uses `~asyncio.get_event_loop`.
    timeout
        Time after which the command will be marked done. Note that if the
        command is not a broadcast and it receives replies to each one of the
        messages it sends, the command will be marked done and the timer
        cancelled. If negative, the command runs forever or until all the
        replies have been received.
    done_callback
        A function to call when the command has been successfully completed.
    n_positioners
        If the command is a broadcast, the number of positioners that should
        reply. If defined, the command will be done once as many positioners
        have replied. Otherwise it waits for the command to time out.
    data
        The data to pass to the messages. It must be a list in which each
        element is the payload for a message. As many messages as data elements
        will be sent. If `None`, a single message without payload will be sent.

    """

    #: The id of the command.
    command_id: CommandID
    #: Whether the command can be broadcast to all robots.
    broadcastable: bool = False
    #: The default timeout for this command.
    timeout = 5
    #: Whether it's safe to execute this command when the FPS is locked.
    safe = False
    #: Whether this command produces a positioner move.
    move_command = False
    #: Whether the command is safe to be issues in bootloader mode.
    bootloader = False

    _interfaces: Optional[List[can.BusABC]]
    _bus: Optional[int]

    def __init__(
        self,
        positioner_id: int,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        timeout: Optional[float] = None,
        done_callback: Optional[Callable] = None,
        n_positioners: Optional[int] = None,
        data: Optional[List[bytearray]] = None,
    ):

        global COMMAND_UID

        assert self.broadcastable is not None, "broadcastable not set"
        assert self.command_id is not None, "command_id not set"

        self.positioner_id = positioner_id
        if self.positioner_id == 0 and self.broadcastable is False:
            raise JaegerError(f"Command {self.command_id.name} cannot be broadcast.")

        self.loop = loop or asyncio.get_event_loop()

        #: The data payload for the messages to send.
        self.data = data or []
        if not isinstance(self.data, (list, tuple)):
            self.data = [self.data]

        #: A list of messages with the responses to this command.
        self.replies: List[Reply] = []

        # Numbers of messages to send. If the command is not a broadcast,
        # the command will be marked done after receiving this many replies.
        self.n_messages = None

        # Generate a UUID for this command.
        self.command_uid = COMMAND_UID
        COMMAND_UID += 1

        # Starting time
        self.start_time = None

        # Stores the UIDs of the messages sent for them to be compared with
        # the replies.
        self.message_uids = []

        uid_bits = config["positioner"]["uid_bits"]
        pool = UID_POOL[self.command_id]
        if self.positioner_id != 0 and self.positioner_id not in pool:
            pool[self.positioner_id] = set(range(1, 2 ** uid_bits))

        if n_positioners is not None and not self.is_broadcast:
            raise JaegerError("n_positioners must be used with a broadcast.")
        self.n_positioners = n_positioners

        self.timeout = timeout if timeout is not None else self.timeout

        # What interface and bus this command should be sent to. Only relevant
        # for multibus interfaces. To be filled by the FPS class when queueing
        # the command.
        self._interface = None
        self._bus = None

        self._done_callback = done_callback

        self._override = False

        self._timeout_handle = None

        self.reply_queue = AsyncQueue(callback=self.process_reply)

        StatusMixIn.__init__(
            self,
            maskbit_flags=CommandStatus,
            initial_status=CommandStatus.READY,
            callback_func=self.status_callback,
        )

        asyncio.Future.__init__(self, loop=self.loop)

    def __repr__(self):
        return (
            f"<Command {self.command_id.name} "
            f"(positioner_id={self.positioner_id}, "
            f"status={self.status.name!r})>"
        )

    def _log(
        self,
        msg,
        level=logging.DEBUG,
        command_id=None,
        positioner_id=None,
        logs=[can_log],
    ):
        """Logs a message."""

        command_id = command_id or self.command_id
        c_name = command_id.name
        pid = positioner_id or self.positioner_id

        msg = f"({c_name}, {pid}, {self.command_uid!s}): " + msg

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

        uids = sorted(self.message_uids)
        replies_uids = sorted([reply.uid for reply in self.replies])
        n_messages = self.n_messages

        assert n_messages, "Number of messages not set."

        if self.is_broadcast:

            if self.n_positioners is None:
                return None
            else:
                uids = sorted(uids * self.n_positioners)
                n_messages *= self.n_positioners

        if n_messages is None or len(self.replies) < n_messages:
            return None

        if len(self.replies) > n_messages:
            self._log(
                "command received more replies than messages. "
                "This should not be possible.",
                level=logging.ERROR,
            )
            self.finish_command(CommandStatus.FAILED)
            return None

        # Compares each message-reply UID.
        if not uids == replies_uids:
            self._log(
                "the UIDs of the messages and replies do not match.",
                level=logging.ERROR,
            )
            return False

        return True

    def process_reply(self, reply_message):
        """Watches the reply queue."""

        reply = Reply(reply_message, command=self)

        # Return the UID to the pool
        if self.positioner_id != 0:
            UID_POOL[self.command_id][self.positioner_id].add(reply.uid)

        if self.status == CommandStatus.TIMEDOUT:
            return
        elif (
            self.status not in [CommandStatus.RUNNING, CommandStatus.CANCELLED]
            and self.timeout > 0
        ):
            # We add CANCELLED because when a command is cancelled replies
            # can arrive later. That's ok and not an error.
            self._log(
                "received a reply but command is not running",
                level=logging.ERROR,
                logs=[log, can_log],
            )
            return

        if self.positioner_id != 0:
            if reply.positioner_id != self.positioner_id:
                raise CommandError("received a reply from a different positioner.")

        self.replies.append(reply)

        data_hex = binascii.hexlify(reply.data).decode()
        self._log(
            f"positioner {reply.positioner_id} replied with "
            f"id={reply.message.arbitration_id}, "
            f"UID={reply.uid}, "
            f"code={reply.response_code.name!r}, "
            f"data={data_hex!r}"
        )

        if reply.response_code != ResponseCode.COMMAND_ACCEPTED:

            self._log(
                f"command failed with code {reply.response_code} "
                f"({reply.response_code.name}).",
                level=logging.ERROR,
            )

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

    def finish_command(self, status: CommandStatus, silent: bool = False):
        """Cancels the queue watcher and removes the running command.

        Parameters
        ----------
        status
            The status to set the command to. If `None` the command will be set
            to `~.CommandStatus.DONE` if one reply for each message has been
            received, `~.CommandStatus.FAILED` otherwise.
        silent
            If `True`, issues error log messages as debug.

        """

        pid = self.positioner_id

        if self._timeout_handle:
            self._timeout_handle.cancel()

        self._status = status

        self.reply_queue.watcher.cancel()

        if not self.done():

            self.set_result(self)

            is_done = self.status == CommandStatus.DONE or (
                pid == 0 and self.status == CommandStatus.TIMEDOUT
            )

            if is_done and self._done_callback:
                if asyncio.iscoroutinefunction(self._done_callback):
                    asyncio.create_task(self._done_callback())
                else:
                    self._done_callback()

            if pid != 0 and self.status == CommandStatus.TIMEDOUT:
                level = logging.WARNING if not silent else logging.DEBUG
                self._log(
                    "this command timed out and it is not a broadcast.", level=level
                )
            elif self.status == CommandStatus.CANCELLED:
                self._log("command has been cancelled.", logging.DEBUG)
            elif self.status.failed:
                level = logging.ERROR if not silent else logging.DEBUG
                self._log(
                    f"command finished with status {self.status.name!r}", level=level
                )

            # For good measure we return all the UIDs
            if pid != 0:
                for uid in self.message_uids:
                    UID_POOL[self.command_id][pid].add(uid)

            self._log(f"finished command with status {self.status.name!r}")

    def status_callback(self):
        """Callback for change status.

        When the status gets set to `.CommandStatus.RUNNING` starts a timer
        that marks the command as done after `.timeout`.

        """

        self._log(f"status changed to {self.status.name}")

        if self.status == CommandStatus.RUNNING:
            self.start_time = time.time()
            if self.timeout is None or self.timeout < 0:
                pass
            elif self.timeout == 0:
                self.finish_command(CommandStatus.TIMEDOUT)
            else:
                self._timeout_handle = self.loop.call_later(
                    self.timeout, self.finish_command, CommandStatus.TIMEDOUT
                )
        elif self.status.is_done:
            self.finish_command(self.status)  # type: ignore

    def _generate_messages_internal(self, data: Optional[List[bytearray]] = None):
        """Generates the list of messages to send to the bus for this command.

        This method is called by `.get_messages` and can be overridden in
        subclasses. Do not override `.get_messages` directly.

        """

        pid = self.positioner_id
        cid = self.command_id

        data = data or self.data

        if len(data) == 0:
            data = [bytearray([])]

        messages = []

        for ii, data_chunk in enumerate(data):

            try:

                if pid != 0:
                    uid = UID_POOL[cid][pid].pop()
                else:
                    uid = 0

            except KeyError:

                # Before failing, put back the UIDs of the other messages
                if pid != 0:
                    for message in messages:
                        UID_POOL[cid][pid].add(message.uid)

                raise CommandError("no UIDs left in the pool.")

            messages.append(Message(self, positioner_id=pid, uid=uid, data=data_chunk))

        return messages

    def get_messages(self, data=None):
        """Returns the list of messages associated with this command.

        Unless overridden, returns a single message with the associated data.

        """

        messages = self._generate_messages_internal(data=data)

        self.n_messages = len(messages)
        self.message_uids = [message.uid for message in messages]

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

    def cancel(self, silent=False, msg=None):
        """Cancels a command, stopping the reply queue watcher."""

        self.finish_command(CommandStatus.CANCELLED, silent=silent)
