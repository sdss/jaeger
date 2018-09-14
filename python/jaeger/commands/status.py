#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-14 14:52:05


from jaeger.commands import Command, CommandID, Message


class GetID(Command):
    """Commands the positioners to reply with their positioner id.

    Parameters
    ----------
    positioner_id : int or list
        The id or list of ids of the robot(s) to which this command will be
        sent. Use ``positioner_id=0`` to broadcast to all robots.

    Attributes
    ----------
    timeout : float
        Time that the command can be running before it is marked as done.

    """

    command_id = CommandID.GET_ID
    broadcastable = True
    timeout = 1.

    def get_messages(self):
        """Returns the messages to send associated with this command."""

        return [Message(self, positioner_id=self.positioner_id, data=[])]

    def get_ids(self):
        """Returns a list of positioners that replied back."""

        return [reply.positioner_id for reply in self.replies]
