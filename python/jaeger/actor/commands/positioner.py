#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-05-13
# @Filename: commands.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import pathlib

import click
import clu
import numpy

from jaeger.commands import SetCurrent

from . import jaeger_parser


def check_positioners(positioner_ids, command, fps, initialised=False):
    """Checks if some of the positioners are not connected."""

    if any([pid not in fps.positioners for pid in positioner_ids]):
        command.failed('some positioners are not connected.')
        return False

    if initialised:
        if any([not fps[pid].initialised for pid in positioner_ids]):
            command.failed('some positioners are not initialised.')
            return False

    return True


@jaeger_parser.command()
@click.argument('POSITIONER-ID', type=int, nargs=-1)
@click.argument('alpha', type=click.FloatRange(0., 360.))
@click.argument('beta', type=click.FloatRange(0., 360.))
@click.option('--speed', type=click.FloatRange(0., 2000.), nargs=2)
@click.option('-a', '--all', is_flag=True, default=False,
              help='applies to all valid positioners.')
@click.option('-f', '--force', is_flag=True, default=False,
              help='forces a move to happen.')
async def goto(command, fps, positioner_id, alpha, beta, speed, all, force):
    """Sends positioners to a given (alpha, beta) position."""

    if all:
        if not force:
            return command.failed('need to specify --force to move '
                                  'all positioners at once.')
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=True):
        return

    speed = speed or [None, None]

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].goto(alpha, beta,
                                               alpha_speed=speed[0],
                                               beta_speed=speed[1]))

    result = await clu.as_complete_failer(tasks, on_fail_callback=fps.abort_trajectory)

    if not result[0]:
        error_message = result[1] or 'goto command failed'
        command.set_status(clu.CommandStatus.FAILED, text=error_message)
    else:
        command.set_status(clu.CommandStatus.DONE, text='Position reached')


@jaeger_parser.command()
@click.argument('positioner-id', type=int, nargs=-1)
@click.option('--datums', is_flag=True, help='If set, initialises the datums.')
async def initialise(command, fps, positioner_id, datums=False):
    """Initialises positioners."""

    if not check_positioners(positioner_id, command, fps):
        return

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].initialise(initialise_datums=datums))

    result = await clu.as_complete_failer(tasks)

    if not result[0]:
        error_message = result[1] or 'initialise failed'
        command.set_status(clu.CommandStatus.FAILED, text=error_message)
    else:
        command.set_status(clu.CommandStatus.DONE, text='Initialisation complete')


@jaeger_parser.command()
@click.argument('positioner-id', type=int, nargs=-1, required=False)
@click.option('-f', '--full', is_flag=True, default=False, help='outputs more statuses.')
@click.pass_context
async def status(ctx, command, fps, positioner_id, full):
    """Reports the position and status bit of a list of positioners."""

    positioner_ids = positioner_id or list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps):
        return

    for pid in positioner_ids:
        positioner = fps[pid]
        alpha_pos = -999 if positioner.alpha is None else numpy.round(positioner.alpha, 4)
        beta_pos = -999 if positioner.beta is None else numpy.round(positioner.beta, 4)
        command.write('i', status=[positioner.positioner_id,
                                   alpha_pos,
                                   beta_pos,
                                   int(positioner.status),
                                   positioner.initialised,
                                   positioner.is_bootloader() or False,
                                   positioner.firmware or '?'])

    if full:
        await clu.Command('wago status', parent=command).parse()

    command.set_status(clu.CommandStatus.DONE)


@jaeger_parser.command()
@click.argument('positioner-id', type=int, nargs=-1, required=False)
@click.argument('alpha', type=click.FloatRange(0., 100.))
@click.argument('beta', type=click.FloatRange(0., 100.))
@click.option('-a', '--all', is_flag=True, default=False,
              help='applies to all connected positioners.')
@click.pass_context
async def current(ctx, command, fps, positioner_id, alpha, beta, all):
    """Reports the position and status bit of a list of positioners."""

    if all:
        positioner_id = [pid for pid in fps.positioners if fps[pid].initialised]

    if len(positioner_id) == 0:
        command.failed('no positioners provided.')
        return

    if not check_positioners(positioner_id, command, fps):
        return

    commands = [fps.send_command(SetCurrent(positioner_id=pid,
                                            alpha=alpha, beta=beta))
                for pid in positioner_id]
    await asyncio.gather(*commands)

    command.done(text='current changed.')


@jaeger_parser.command()
@click.argument('path', type=str)
async def trajectory(command, fps, path):
    """Sends a trajectory from a file."""

    path = pathlib.Path(path).expanduser()
    if not path.exists():
        raise click.BadParameter(f'path {path!s} does not exist.')

    await fps.send_trajectory(str(path))

    command.set_status(clu.CommandStatus.DONE, text='Trajectory completed')
