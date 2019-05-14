#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-05-13
# @Filename: commands.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-05-13 17:56:58

import click

from clu import command_parser as jaeger_parser


@jaeger_parser.command()
@click.argument('positioner-id', type=int)
@click.argument('alpha', type=click.FloatRange(0., 360.))
@click.argument('beta', type=click.FloatRange(0., 360.))
@click.option('--speed', type=click.FloatRange(0., 2000.), nargs=2)
async def goto(command, fps, positioner_id, alpha, beta, speed=None):
    """Sends a positioner to a given (alpha, beta) position."""

    speed = speed or [None, None]

    await fps.positioners[positioner_id].goto(alpha, beta,
                                              alpha_speed=speed[0],
                                              beta_speed=speed[1])
