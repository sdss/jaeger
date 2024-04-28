#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-03-24
# @Filename: talk.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import click

from jaeger.commands import CommandID

from . import jaeger_parser


__all__ = ["talk"]


@jaeger_parser.command()
@click.argument("COMMAND_ID", nargs=1, type=int)
@click.argument("POSITIONER_ID", nargs=1, type=int)
@click.argument("PARAMS", nargs=-1)
async def talk(command, fps, command_id, positioner_id, params):
    """Send a direct command to the CAN network and show the replies."""

    command_id = CommandID(command_id)
    assert isinstance(command_id, CommandID)

    CommandClass = command_id.get_command_class()
    assert CommandClass

    can_command = CommandClass(positioner_id, *params)

    command.info(f"Running command {can_command.command_id.name}.")

    await fps.send_command(can_command)

    replies = can_command.replies

    for reply in replies:
        data = '"'
        for byte in reply.data:
            data += f"\\x{byte:02x}"
        data += '"'
        command.info(
            {
                "raw": [
                    reply.command_id.value,
                    reply.uid,
                    reply.response_code.value,
                    data,
                ]
            }
        )

    return command.finish()
