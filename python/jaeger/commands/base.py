#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: base.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-08-30 09:07:44

import abc

import can
from jaeger import log
from jaeger.core import exceptions


__ALL__ = ['StatusMixIn', 'JaegerMessage', 'CANCommand']


class StatusMixIn(object):
    """A mixin that provides status tracking with callbacks.

    Provides a status property that executes a list of callbacks when
    the status changes.

    Parameters
    ----------
    initial_status : str
        The initial status.
    callback_func : function
        The function to call if the status changes.
    call_now : bool
        Whether the callback function should be called when initialising.

    Attributes
    ----------
    callbacks : list
        A list of the callback functions to call.

    """

    READY = 'ready'
    EXECUTING = 'executing'
    DONE = 'done'
    FAILED = 'failed'

    VALID_STATUS = [READY, EXECUTING, DONE, FAILED]

    def __init__(self, initial_status=None, callback_func=None, call_now=False):

        assert len(self.VALID_STATUS) > 0, 'no VALID_STATUS defined.'

        self.callbacks = []
        self._status = initial_status

        if callback_func is not None:
            self.callbacks.append(callback_func)

        if call_now is True:
            self.do_callbacks()

    def do_callbacks(self):
        """Calls functions in `.StatusMixIn.callbacks`."""

        assert hasattr(self, 'callbacks'), 'missing callbacks attribute. Did you call __init__()?'

        for func in self.callbacks:
            func()

    @property
    def status(self):
        """Returns the status."""

        return self._status

    @status.setter
    def status(self, value):
        """Sets the status."""

        assert value in self.VALID_STATUS, 'invalid status'

        if value != self._status:
            self._status = value
            self.do_callbacks()


class Message(can.Message, StatusMixIn):
    """An extended `can.Message` class that provides status tracking.

    Expands the `can.Message` class by subclassing from `.StatusMixIn`. The
    status is set to ``READY`` on init.

    Parameters
    ----------
    data : list or bytearray
        Payload to pass to `can.Message`.
    arbitration_id : int
        The id to which the message will be sent (0 for broadcast).
    extended_id : bool
        Whether the id is an 11 bit (False) or 29 bit (True) address.
    callback_func : function
        The callback function to call when the status changes.

    """

    def __init__(self, data, arbitration_id=0, extended_id=False, callback_func=None):

        can.Message.__init__(self,
                             data=data,
                             arbitration_id=arbitration_id,
                             extended_id=extended_id)

        StatusMixIn.__init__(self, initial_status=self.READY, callback_func=callback_func)


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
    arbitration_id : int or list
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``robot_id=0`` to broadcast to all robots.
    callback_func : function
        The callback function to call when the status changes.

    Attributes
    ----------
    broadcastable : bool
        Whether the command can be broadcast to all robots.
    command_id : int
        The id of the command.
    replies : list
        A list of the replies received by this command.

    """

    command_id = None
    broadcastable = None

    @abc.abstractmethod
    def __init__(self, arbitration_id=0, callback_func=None):

        assert self.broadcastable is not None, 'broadcastable not set'
        assert self.command_id is not None, 'command_id not set'

        self.arbitration_id = arbitration_id
        if self.arbitration_id == 0 and self.broadcastable is False:
            raise exceptions.JaegerError('this command cannot be broadcast.')

        self.replies = []

        StatusMixIn.__init__(self, initial_status=self.READY, callback_func=callback_func)

    @abc.abstractmethod
    def get_messages(self):
        """Returns the list of messages associated with this command."""

        pass

    def send(self, bus, force=False):
        """Sends the command.

        Writes each message to the bus in turn and waits for a response.

        Parameters
        ----------
        bus : `~jaeger.can.BaseCAN`
            The CAN interface bus.
        force : bool
            If the command has already been finished, sending it will fail
            unless ``force=True``.

        """

        if self.status in (self.DONE, self.FAILED):
            if force is False:
                raise exceptions.JaegerError(
                    f'command {self.command_id}: trying to send a done command.')
            else:
                log.info(
                    f'command {self.command_id}: command is done but force=True. '
                    'Making command ready again.')
                self.status = self.READY

        bus.send_command(self)
