#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: status.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-18 20:25:22


from jaeger.commands import Command, CommandID


class GetID(Command):
    """Commands the positioners to reply with their positioner id."""

    command_id = CommandID.GET_ID
    broadcastable = True
    timeout = 1.

    def get_ids(self):
        """Returns a list of positioners that replied back."""

        return [reply.positioner_id for reply in self.replies]
