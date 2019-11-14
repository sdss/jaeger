#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: cli.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import logging
import warnings
from functools import wraps

import click
import numpy

from jaeger import config, log
from jaeger.commands.bootloader import load_firmware
from jaeger.fps import FPS
from jaeger.maskbits import PositionerStatus
from jaeger.testing import VirtualFPS


def cli_coro(f):
    """Decorator function that allows defining coroutines with click."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(f(*args, **kwargs))

    return wrapper


@click.group(invoke_without_command=True)
@click.option('-p', '--profile', type=str, help='The bus interface profile.')
@click.option('-l', '--layout', type=str, help='The FPS layout.')
@click.option('-v', '--verbose', is_flag=True, help='Debug mode.')
@click.option('--no-tron', is_flag=True, help='Does not connect to Tron.')
@click.option('--wago/--no-wago', default=None, help='Does not connect to the WAGO.')
@click.option('--qa/--no-qa', default=None, help='Does not use the QA database.')
@click.pass_context
@cli_coro
async def jaeger(ctx, layout, profile, verbose, no_tron, wago, qa):
    """CLI for the SDSS-V focal plane system.

    If called without subcommand starts the actor.

    """

    if verbose:
        log.set_level(logging.DEBUG)

    # If profile is test we start a VirtualFPS first so that it can respond
    # to the FPS class.
    if profile == 'test':
        VirtualFPS('test', layout=layout)

    ctx.obj = {}
    ctx.obj['can_profile'] = profile
    ctx.obj['layout'] = layout

    fps = FPS(**ctx.obj, wago=wago, qa=qa)
    await fps.initialise()
    ctx.obj['fps'] = fps

    # If we call jaeger without a subcommand and with the actor flag,
    # start the actor.
    if ctx.invoked_subcommand is None:

        try:
            from jaeger.actor import JaegerActor
        except ImportError:
            raise ImportError('CLU needs to be installed to run jaeger as an actor.')

        actor_config = config['actor'].copy()
        actor_config.pop('status', None)

        if no_tron:
            actor_config.pop('tron', None)

        actor = await JaegerActor.from_config(actor_config, fps).start()
        await actor.start_status_server(config['actor']['status']['port'],
                                        delay=config['actor']['status']['delay'])

        await actor.run_forever()


@jaeger.command(name='upgrade-firmware')
@click.argument('firmware-file', nargs=1, type=click.Path(exists=True))
@click.option('-f', '--force', is_flag=True, help='Forces skipping of invalid positioners')
@click.option('-s', '--positioners', type=str, help='Comma-separated positioners to upgrade')
@click.pass_context
@cli_coro
async def upgrade_firmware(ctx, firmware_file, force=False, positioners=None):
    """Upgrades the firmaware."""

    if positioners is not None:
        positioners = [int(positioner.strip()) for positioner in positioners.split(',')]

    fps = ctx.obj['fps']

    await load_firmware(fps, firmware_file, positioners=positioners,
                        force=force, show_progressbar=True)


@jaeger.command()
@click.argument('positioner_id', metavar='POSITIONER', type=int)
@click.argument('alpha', metavar='ALPHA', type=float)
@click.argument('beta', metavar='BETA', type=float)
@click.option('--speed', type=(float, float), default=(1000, 1000),
              help='The speed for the alpha and beta motors.',
              show_default=True)
@click.pass_context
@cli_coro
async def goto(ctx, positioner_id, alpha, beta, speed=None):
    """Moves a robot to a given position."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError('alpha must be in the range [0, 360)')

    if beta < 0 or beta >= 360:
        raise click.UsageError('beta must be in the range [0, 360)')

    if speed[0] < 0 or speed[0] >= 3000 or speed[1] < 0 or speed[1] >= 3000:
        raise click.UsageError('speed must be in the range [0, 3000)')

    fps = ctx.obj['fps']

    positioner = fps.positioners[positioner_id]
    result = await positioner.initialise()
    if not result:
        log.error('positioner is not connected or failed to initialise.')
        return

    result = await positioner.goto(alpha=alpha, beta=beta,
                                   alpha_speed=speed[0],
                                   beta_speed=speed[1])

    if result is False:
        return


@jaeger.command(name='set-positions')
@click.argument('positioner_id', metavar='POSITIONER', type=int)
@click.argument('alpha', metavar='ALPHA', type=float)
@click.argument('beta', metavar='BETA', type=float)
@click.pass_context
@cli_coro
async def set_positions(ctx, positioner_id, alpha, beta):
    """Sets the position of the alpha and beta arms."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError('alpha must be in the range [0, 360)')

    if beta < 0 or beta >= 360:
        raise click.UsageError('beta must be in the range [0, 360)')

    fps = ctx.obj['fps']

    positioner = fps.positioners[positioner_id]

    result = await positioner.set_position(alpha, beta)

    if not result:
        log.error('failed to set positions.')
        return

    log.info(f'positioner {positioner_id} set to {alpha, beta}.')


@jaeger.command()
@click.argument('positioner_id', metavar='POSITIONER', type=int)
@click.option('-n', '--moves', type=int,
              help='Number of moves to perform. Otherwise runs forever.')
@click.option('--alpha', type=(int, int), default=(0, 360),
              help='Range of alpha positions.', show_default=True)
@click.option('--beta', type=(int, int), default=(0, 180),
              help='Range of beta positions.', show_default=True)
@click.option('--speed', type=(int, int), default=(500, 1500),
              help='Range of speed.', show_default=True)
@click.option('-f', '--skip-errors', is_flag=True,
              help='If an error occurs, ignores it and '
                   'commands another move.')
@click.pass_context
@cli_coro
async def demo(ctx, positioner_id, alpha=None, beta=None, speed=None, moves=None,
               skip_errors=False):
    """Moves a robot to random positions."""

    if (alpha[0] >= alpha[1]) or (alpha[0] < 0 or alpha[1] > 360):
        raise click.UsageError('alpha must be in the range [0, 360)')

    if (beta[0] >= beta[1]) or (beta[0] < 0 or beta[1] > 360):
        raise click.UsageError('beta must be in the range [0, 360)')

    if (speed[0] >= speed[1]) or (speed[0] < 0 or speed[1] >= 3000):
        raise click.UsageError('speed must be in the range [0, 3000)')

    fps = ctx.obj['fps']

    positioner = fps.positioners[positioner_id]
    result = await positioner.initialise()
    if not result:
        log.error('positioner is not connected or failed to initialise.')
        return

    done_moves = 0
    while True:

        alpha_move = numpy.random.randint(low=alpha[0], high=alpha[1])
        beta_move = numpy.random.randint(low=beta[0], high=beta[1])
        alpha_speed = numpy.random.randint(low=speed[0], high=speed[1])
        beta_speed = numpy.random.randint(low=speed[0], high=speed[1])

        warnings.warn(f'running step {done_moves+1}')

        result = await positioner.goto(alpha=alpha_move, beta=beta_move,
                                       alpha_speed=alpha_speed,
                                       beta_speed=beta_speed)

        if result is False:
            if skip_errors is False:
                return
            else:
                warnings.warn('an error happened but ignoring it '
                              'because skip-error=True')
                continue

        done_moves += 1

        if moves is not None and done_moves == moves:
            return


@jaeger.command()
@click.argument('positioner_id', metavar='POSITIONER', type=int, required=False)
@click.pass_context
@cli_coro
async def home(ctx, positioner_id):
    """Initialise datums."""

    fps = ctx.obj['fps']

    if positioner_id is None:
        positioners = fps.positioners.values()
    else:
        positioners = [fps.positioners[positioner_id]]

    valid_positioners = [positioner for positioner in positioners
                         if PositionerStatus.SYSTEM_INITIALIZATION in positioner.status]

    if len(valid_positioners) < len(positioners):
        warnings.warn(f'{len(positioners) - len(valid_positioners)} positioners '
                      'have not been initialised and will not be homed.')

    await asyncio.gather(*[
        fps.send_command('INITIALIZE_DATUMS', positioner_id=pos.positioner_id)
        for pos in valid_positioners])

    return
