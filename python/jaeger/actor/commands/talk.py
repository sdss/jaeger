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


@jaeger_parser.command()
@click.argument("PARAMS", nargs=-1)
async def talk(command, fps, params):
    """Send a direct command to the CAN network and show the replies."""

    command_id, *extra_args = params
    CommandClass = CommandID(command_id).get_command_class()
    can_command = CommandClass(*extra_args)

    command.info(f"Running command {can_command.command_id.name}.")
    await can_command

    replies = can_command.replies

    for reply in replies:
        command.info(
            {
                "raw": [
                    reply.command_id.value,
                    reply.uid,
                    reply.response_code.value,
                    reply.data.decode(),
                ]
            }
        )

    return command.finish()
