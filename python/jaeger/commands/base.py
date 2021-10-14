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
import sys
import time
import warnings

from typing import Any, Callable, Dict, List, Optional, Union

from jaeger import can_log, config, log, maskbits
from jaeger.exceptions import CommandError, JaegerError, JaegerUserWarning
from jaeger.interfaces import BusABC, Message
from jaeger.maskbits import CommandStatus, ResponseCode
from jaeger.utils import StatusMixIn, get_identifier, parse_identifier

from . import CommandID


__all__ = ["SuperMessage", "Command", "EmptyPool"]


# A pool of UIDs that can be assigned to each command for a given command_id.
# The format of the pool is UID_POOL[command_id][positioner_id] so that each
# positioner has uid_bits (64) messages for each command id. UID=0 is always
# reserved for broadcasts.
UID_POOL = collections.defaultdict(dict)

# Starting value for command UID.
COMMAND_UID = 0


class SuperMessage(Message):
    """An extended CAN ``Message`` class.

    Expands the ``Message`` class to handle custom arbitration IDs for
    extended frames.

    Parameters
    ----------
    command
        The command associated with this message.
    data
        Payload to pass to ``Message``.
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

        Message.__init__(
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

    def __init__(self, message: Message, command: Optional[Command] = None):

        assert isinstance(message, Message), "invalid message"

        #: The command for which this reply is intended.
        self.command = command

        #: The raw ``Message``.
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


class EmptyPool(CommandError):
    pass


data_co = Union[None, bytearray, List[bytearray]]


if sys.version_info >= (3, 9):
    Future_co = asyncio.Future["Command"]
else:
    Future_co = asyncio.Future


class Command(StatusMixIn[CommandStatus], Future_co):
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
    positioner_ids
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``positioner_ids=0`` to broadcast to all robots.
    loop
        The running event loop, or uses `~asyncio.get_event_loop`.
    timeout
        Time after which the command will be marked done when not all the
        positioners have replies. If `None`, the default timeout will be used.
        If timeout is a negative number, the command won't timeout until all
        the positioners have replied.
    done_callback
        A function to call when the command has been successfully completed.
    n_positioners
        If the command is a broadcast, the number of positioners that should
        reply. If defined, the command will be done once as many positioners
        have replied. Ignored for non-broadcasts.
    data
        The data to pass to the messages. If a list, each element will be
        sent to each positioner as a message. It can also be a dictionary of
        lists in which the key is the positioner to which to send the data.
    ignore_unknown
        Ignores ``UNKNOWN_COMMAND`` replies from positioners that do now
        support this command.

    """

    #: The id of the command.
    command_id: CommandID
    #: Whether the command can be broadcast to all robots.
    broadcastable: bool = False
    #: The default timeout for this command.
    timeout: float = 5
    #: Whether it's safe to execute this command when the FPS is locked.
    safe = False
    #: Whether this command produces a positioner move.
    move_command = False
    #: Whether the command is safe to be issues in bootloader mode.
    bootloader = False

    def __init__(
        self,
        positioner_ids: int | List[int],
        timeout: Optional[float] = None,
        done_callback: Optional[Callable] = None,
        n_positioners: Optional[int] = None,
        data: Union[None, data_co, Dict[int, data_co]] = None,
        ignore_unknown: bool = True,
    ):

        global COMMAND_UID

        assert self.broadcastable is not None, "broadcastable not set"
        assert self.command_id is not None, "command_id not set"

        if isinstance(positioner_ids, (list, tuple)):
            self.positioner_ids = list(positioner_ids)
            if len(positioner_ids) != len(set(positioner_ids)):
                raise JaegerError("The list of positioner_ids must be unique.")
            if len(positioner_ids) > 1 and 0 in positioner_ids:
                raise JaegerError("Broadcasts cannot be mixed with other positioners.")
        else:
            self.positioner_ids = [positioner_ids]

        if self.is_broadcast and self.broadcastable is False:
            raise JaegerError(f"Command {self.command_id.name} cannot be broadcast.")

        #: The data payload for the messages to send.
        if data is None:
            self.data = {pid: [bytearray()] for pid in self.positioner_ids}
        elif isinstance(data, bytearray):
            self.data = {pid: [data] for pid in self.positioner_ids}
        elif isinstance(data, (list, tuple)):
            self.data = {pid: data for pid in self.positioner_ids}
        elif isinstance(data, dict):
            self.data = {}
            for pid, value in data.items():
                if value is None:
                    self.data[pid] = [bytearray()]
                elif isinstance(value, (list, tuple)):
                    self.data[pid] = value
                elif isinstance(value, bytearray):
                    self.data[pid] = [value]
                else:
                    raise ValueError(f"Invalid data {value!r}.")
        else:
            raise ValueError(f"Invalid data {data!r}.")

        if self.is_broadcast:
            if len(self.data[0]) > 1:
                raise CommandError("Broadcasts can only include a single data packet.")

        # Number of replies expected unless broadcast.
        if self.is_broadcast:
            if n_positioners:
                self._n_replies = len(self.data[0]) * n_positioners
            else:
                self._n_replies = None
        else:
            self._n_replies = sum([len(value) for value in self.data.values()])

        if timeout is None:
            pass
        else:
            self.timeout = timeout
            if self.timeout < 0 and self._n_replies is None:
                raise CommandError(
                    "In a broadcast a timeout is required unless n_positioners is set."
                )

        #: A list of messages with the responses to this command.
        self.replies: List[Reply] = []

        # Messages sent.
        self.messages = []
        self.message_uids = []

        # Generate a UUID for this command.
        self.command_uid = COMMAND_UID
        COMMAND_UID += 1

        # Starting and end time
        self.start_time: float | None = None
        self.end_time: float | None = None

        # If this is the first time we run this command the pool will be empty.
        uid_bits = config["positioner"]["uid_bits"]
        command_pool = UID_POOL[self.command_id]
        if not self.is_broadcast:
            for pid in self.positioner_ids:
                if pid not in command_pool:
                    command_pool[pid] = set(range(1, 2 ** uid_bits))
        else:
            if 0 not in command_pool:
                command_pool[0] = set([0])

        # What interface and bus this command should be sent to. Only relevant
        # for multibus interfaces. To be filled by the FPS class when queueing
        # the command.
        self._interfaces: Optional[List[BusABC]] = []
        self._bus: Optional[int] = None

        self._done_callback = done_callback

        self._timeout_handle = None

        self._ignore_unknown = ignore_unknown
        self.loop = asyncio.get_event_loop()

        StatusMixIn.__init__(
            self,
            maskbit_flags=CommandStatus,
            initial_status=CommandStatus.READY,
            callback_func=self.status_callback,
        )

        asyncio.Future.__init__(self)

    def __repr__(self):
        return (
            f"<Command {self.command_id.name} "
            f"(positioner_ids={self.positioner_ids!r}, "
            f"status={self.status.name!r})>"
        )

    def _log(
        self,
        msg,
        level=logging.DEBUG,
        command_id=None,
        positioner_ids=None,
        logs=[can_log],
    ):
        """Logs a message."""

        command_id = command_id or self.command_id
        c_name = command_id.name
        pid = positioner_ids or self.positioner_ids

        msg = f"[{c_name}, {pid}, {self.command_uid!s}]: " + msg

        for ll in logs:
            ll.log(level, msg)

    @property
    def is_broadcast(self):
        """Returns `True` if the command is a broadcast."""

        return self.positioner_ids == [0]

    @property
    def name(self):
        """Returns the name of this command."""

        return CommandID(self.command_id).name

    def _check_replies(self):
        """Checks if the UIDs of the replies match the messages."""

        sent_uids = self.message_uids
        replies_uids = [reply.uid for reply in self.replies]

        if self.is_broadcast:
            if self._n_replies is None:  # This means it will timeout.
                return None
            else:
                sent_uids = [0] * self._n_replies

        assert self._n_replies, "_n_replies must be set."

        if len(self.replies) < self._n_replies:
            return None

        if len(self.replies) > self._n_replies:
            self._log(
                "command received more replies than messages. "
                "This should not be possible.",
                level=logging.ERROR,
            )
            return False

        # Compares each message-reply UID.
        if sorted(sent_uids) != sorted(replies_uids):
            self._log(
                "the UIDs of the messages and replies do not match.",
                level=logging.ERROR,
            )
            return False

        return True

    async def process_reply(self, reply_message):
        """Watches the reply queue."""

        reply = Reply(reply_message, command=self)

        # Return the UID to the pool.
        if not self.is_broadcast:
            UID_POOL[self.command_id][reply.positioner_id].add(reply.uid)

        if self.status == CommandStatus.TIMEDOUT:
            self._log(
                "received a reply but the command has already timed out.",
                level=logging.ERROR,
                logs=[log, can_log],
            )
            return
        elif self.status == CommandStatus.CANCELLED:
            return
        elif self.status != CommandStatus.RUNNING:
            self._log(
                "received a reply but command is not running",
                level=logging.ERROR,
                logs=[log, can_log],
            )
            return

        if not self.is_broadcast:
            if reply.positioner_id not in self.positioner_ids:
                self._log(
                    "received a reply from a non-commanded positioner.",
                    level=logging.ERROR,
                    logs=[log, can_log],
                )
                return

        self.replies.append(reply)

        data_hex = binascii.hexlify(reply.data).decode()
        self._log(
            f"positioner {reply.positioner_id} replied with "
            f"id={reply.message.arbitration_id}, "
            f"UID={reply.uid}, "
            f"code={reply.response_code.name!r}, "
            f"data={data_hex!r}"
        )

        code = reply.response_code
        COMMAND_ACCEPTED = ResponseCode.COMMAND_ACCEPTED
        UNKNOWN_COMMAND = ResponseCode.UNKNOWN_COMMAND
        if code != COMMAND_ACCEPTED:
            if not self._ignore_unknown or code != UNKNOWN_COMMAND:
                warnings.warn(
                    f"Positioner {reply.positioner_id} replied to {self.name} "
                    f"UID={self.command_uid} with {code.name!r}.",
                    JaegerUserWarning,
                )

        reply_status = self._check_replies()

        # If reply_status is True then a reply from each commanded positioner has
        # been received. If they are all COMMAND_ACCEPTED, mark the command as done.
        # If some are not COMMAND_ACCEPTED, check the invalid replies are all
        # UNKNOWN_COMMAND. In that case, if we are ignoring those, still mark as done.
        # Otherwise finish as failed.
        # If reply_status is False, that means we have received replies with UIDs that
        # do not match the UID of this command. This should not happens and it's most
        # likely a bug in the code.
        # If reply_status is None, we haven't yet matched the expected number of
        # replies and we just return.

        if reply_status is True:
            reply_codes = [reply.response_code for reply in self.replies]
            invalid = [code for code in reply_codes if code != COMMAND_ACCEPTED]
            ignore_unknown = self._ignore_unknown
            if not all([code == COMMAND_ACCEPTED for code in reply_codes]):
                if all([inv == UNKNOWN_COMMAND for inv in invalid]) and ignore_unknown:
                    self.finish_command(CommandStatus.DONE)
                else:
                    self.finish_command(CommandStatus.FAILED)
            else:
                self.finish_command(CommandStatus.DONE)
        elif reply_status is False:
            self.finish_command(CommandStatus.FAILED)
        else:
            return

    def finish_command(self, status: CommandStatus, silent: bool = False):
        """Finishes a command, marking the Future as done.

        Parameters
        ----------
        status
            The status to set the command to. If `None` the command will be set
            to `~.CommandStatus.DONE` if one reply for each message has been
            received, `~.CommandStatus.FAILED` otherwise.
        silent
            If `True`, issues error log messages as debug.

        """

        if self._timeout_handle:
            self._timeout_handle.cancel()

        self._status = status

        if not self.done():
            level = logging.WARNING if not silent else logging.DEBUG
            if not self.is_broadcast and self.status == CommandStatus.TIMEDOUT:
                self._log("this command timed out and it is not a broadcast.", level)
            elif self.status == CommandStatus.CANCELLED:
                self._log("command has been cancelled.", logging.DEBUG)
            elif self.status.failed:
                level = logging.ERROR if not silent else logging.DEBUG
                self._log(f"command finished with status {self.status.name!r}", level)

            # For good measure we return all the UIDs
            if self.is_broadcast:
                UID_POOL[self.command_id][0].add(0)
            else:
                for message in self.messages:
                    UID_POOL[self.command_id][message.positioner_id].add(message.uid)

            self.set_result(self)
            self.end_time = time.time()

            is_done = self.status in [CommandStatus.TIMEDOUT, CommandStatus.DONE]

            if is_done and self._done_callback:
                if asyncio.iscoroutinefunction(self._done_callback):
                    asyncio.create_task(self._done_callback())
                else:
                    self._done_callback()

            self._log(f"finished command with status {self.status.name!r}")

    def status_callback(self):
        """Callback for change status.

        When the status gets set to `.CommandStatus.RUNNING` starts a timer
        that marks the command as done after `.timeout`.

        """

        self._log(f"status changed to {self.status.name}")

        if self.status == CommandStatus.RUNNING:
            self.start_time = time.time()
            if self.timeout < 0:
                pass
            elif self.timeout == 0:
                self.finish_command(CommandStatus.TIMEDOUT)
            else:
                self._timeout_handle = self.loop.call_later(
                    self.timeout,
                    self.finish_command,
                    CommandStatus.TIMEDOUT,
                )
        elif self.status.is_done and not self.done():
            self.finish_command(self.status)

    def _generate_messages_internal(self, data: Optional[List[bytearray]] = None):
        """Generates the list of messages to send to the bus for this command.

        This method is called by `.get_messages` and can be overridden in
        subclasses. Do not override `.get_messages` directly.

        """

        cid = self.command_id

        messages: List[SuperMessage] = []

        for pid in self.data:

            pid_data = self.data[pid]

            for d in pid_data:

                try:
                    uid = UID_POOL[cid][pid].pop()
                except KeyError:
                    # Before failing, put back the UIDs of the other messages
                    for message in messages:
                        UID_POOL[cid][pid].add(message.uid)
                    raise EmptyPool("no UIDs left in the pool.")

                messages.append(SuperMessage(self, positioner_id=pid, uid=uid, data=d))

        return messages

    def get_messages(self, data=None):
        """Returns the list of messages associated with this command.

        Unless overridden, returns a single message with the associated data.

        """

        if len(self.messages) > 0:
            raise CommandError("Messages have already been sent.")

        messages = self._generate_messages_internal(data=data)

        self.messages = messages
        self.message_uids = [message.uid for message in messages]

        return messages

    def get_replies(self) -> Dict[int, Any]:
        """Returns the formatted replies as a dictionary.

        The values returned will depend on the specific command.

        """

        return {}

    def cancel(self, silent=False, msg=None):
        """Cancels a command, stopping the reply queue watcher."""

        self.finish_command(CommandStatus.CANCELLED, silent=silent)
