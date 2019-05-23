#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-05-13
# @Filename: commands.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-05-22 19:14:44

import click

import clu
from clu import command_parser as jaeger_parser
from jaeger.utils import as_complete_failer


@jaeger_parser.command()
@click.argument('POSITIONER-ID', type=int, nargs=-1)
@click.argument('alpha', type=click.FloatRange(0., 360.))
@click.argument('beta', type=click.FloatRange(0., 360.))
@click.option('--speed', type=click.FloatRange(0., 2000.), nargs=2)
async def goto(command, fps, positioner_id, alpha, beta, speed=None):
    """Sends a positioner to a given (alpha, beta) position."""

    speed = speed or [None, None]

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].goto(alpha, beta,
                                               alpha_speed=speed[0],
                                               beta_speed=speed[1]))

    result = await as_complete_failer(tasks, on_fail_callback=fps.abort_trajectory)

    if not result:
        command.set_status(clu.CommandStatus.FAILED, text='goto failed')
    else:
        command.set_status(clu.CommandStatus.DONE, text='Position reached')


@jaeger_parser.command()
@click.argument('positioner-id', type=int, nargs=-1)
@click.option('--datums', is_flag=True, help='If set, initialises the datums.')
async def initialise(command, fps, positioner_id, datums=False):
    """Sends a positioner to a given (alpha, beta) position."""

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].initialise(initialise_datums=datums))

    result = await as_complete_failer(tasks)

    if not result:
        command.set_status(clu.CommandStatus.FAILED, text='initialise failed')
    else:
        command.set_status(clu.CommandStatus.DONE, text='Initialisation complete')
