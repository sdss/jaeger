#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-13 17:23:33


from jaeger.commands import Command, CommandID, Message


class GetID(Command):

    command_id = CommandID.GET_ID
    broadcastable = True

    def get_messages(self):
        """Returns the messages to send associated with this command."""

        return [Message(self, positioner_id=self.positioner_id, data=[])]
