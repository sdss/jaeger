#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: cli.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import logging
import signal
import sys
import warnings
from functools import wraps

import click
import numpy

from jaeger import log
from jaeger.commands.bootloader import load_firmware
from jaeger.fps import FPS
from jaeger.testing import VirtualFPS


fps = None


def shutdown(loop, sign):
    """Shuts down the FPS and stops the positioners in case of a signal interrupt."""

    if fps:
        fps.send_command('STOP_TRAJECTORY', positioner_id=0, synchronous=True)
        log.error(f'stopping positioners and cancelling due to {sign.name}')
        sys.exit(0)
    else:
        log.error(f'cannot shutdown FPS before {sign.name}')
        sys.exit(1)


def cli_coro(f):
    """Decorator function that allows defining coroutines with click."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        for ss in signals:
            loop.add_signal_handler(ss, shutdown, loop, ss)
        return loop.run_until_complete(f(*args, **kwargs))

    return wrapper


class FPSWrapper(object):
    """A helper to store FPS initialisation parameters."""

    def __init__(self, verbose, profile, layout, wago=None, qa=None, danger=None):

        self.verbose = verbose

        self.profile = profile
        if self.profile in ['test', 'virtual']:
            self.profile = 'virtual'

        self.layout = layout
        self.wago = wago
        self.qa = qa
        self.danger = danger

        self.fps = None

        if self.verbose:
            log.set_level(logging.DEBUG)

    async def __aenter__(self):

        global fps

        # If profile is test we start a VirtualFPS first so that it can respond
        # to the FPS class.
        if self.profile == 'virtual':
            self.fps = VirtualFPS(layout=self.layout)
        else:
            self.fps = FPS(can_profile=self.profile, layout=self.layout,
                           wago=self.wago, qa=self.qa, engineering_mode=self.danger)

        fps = self.fps

        await self.fps.initialise()
        return self.fps

    async def __aexit__(self, *excinfo):
        await self.fps.shutdown()


pass_fps = click.make_pass_decorator(FPSWrapper, ensure=True)


@click.group(invoke_without_command=True)
@click.option('-p', '--profile', type=str, help='The bus interface profile.')
@click.option('-l', '--layout', type=str, help='The FPS layout.')
@click.option('-v', '--verbose', is_flag=True, help='Debug mode.')
@click.option('--no-tron', is_flag=True, help='Does not connect to Tron.')
@click.option('--wago/--no-wago', default=None, help='Does not connect to the WAGO.')
@click.option('--qa/--no-qa', default=None, help='Does not use the QA database.')
@click.option('--danger', is_flag=True,
              help='Enables engineering mode. Most safety checks will be disabled.')
@click.pass_context
@cli_coro
async def jaeger(ctx, layout, profile, verbose, no_tron, wago, qa, danger):
    """CLI for the SDSS-V focal plane system.

    If called without subcommand starts the actor.

    """

    ctx.obj = FPSWrapper(verbose, profile, layout, wago, qa, danger)

    # If we call jaeger without a subcommand and with the actor flag,
    # start the actor.
    if ctx.invoked_subcommand is None:

        try:
            from jaeger.actor import JaegerActor
            from jaeger import config
        except ImportError:
            raise ImportError('CLU needs to be installed to run jaeger as an actor.')

        actor_config = config['actor'].copy()
        actor_config.pop('status', None)

        if no_tron:
            actor_config.pop('tron', None)

        async with ctx.obj:
            actor = await JaegerActor.from_config(actor_config, fps).start()
            await actor.start_status_server(config['actor']['status']['port'],
                                            delay=config['actor']['status']['delay'])

            await actor.run_forever()


@jaeger.command(name='upgrade-firmware')
@click.argument('firmware-file', nargs=1, type=click.Path(exists=True))
@click.option('-f', '--force', is_flag=True, help='Forces skipping of invalid positioners')
@click.option('-s', '--positioners', type=str, help='Comma-separated positioners to upgrade')
@pass_fps
@cli_coro
async def upgrade_firmware(obj, firmware_file, force=False, positioners=None):
    """Upgrades the firmaware."""

    if positioners is not None:
        positioners = [int(positioner.strip()) for positioner in positioners.split(',')]

    async with obj as fps:

        if fps.wago:
            log.info('power cycling positioners')
            await fps.pollers.stop()
            await fps.wago.turn_off('24V')
            await asyncio.sleep(5)
            await fps.wago.turn_on('24V')
            await asyncio.sleep(3)
            await fps.initialise()

        await load_firmware(fps, firmware_file, positioners=positioners,
                            force=force, show_progressbar=True)


@jaeger.command()
@click.argument('positioner_id', metavar='POSITIONER', type=int)
@click.argument('alpha', metavar='ALPHA', type=float)
@click.argument('beta', metavar='BETA', type=float)
@click.option('--speed', type=(float, float), default=(None, None),
              help='The speed for the alpha and beta motors.',
              show_default=True)
@pass_fps
@cli_coro
async def goto(obj, positioner_id, alpha, beta, speed=None):
    """Moves a robot to a given position."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError('alpha must be in the range [0, 360)')

    if beta < 0 or beta >= 360:
        raise click.UsageError('beta must be in the range [0, 360)')

    if speed[0] or speed[1]:
        if speed[0] < 0 or speed[0] >= 3000 or speed[1] < 0 or speed[1] >= 3000:
            raise click.UsageError('speed must be in the range [0, 3000)')

    async with obj as fps:

        positioner = fps.positioners[positioner_id]
        result = await positioner.initialise()
        if not result:
            log.error('positioner is not connected or failed to initialise.')
            return

        await positioner.goto(alpha=alpha, beta=beta, speed=(speed[0], speed[1]))

    return


@jaeger.command(name='set-positions')
@click.argument('positioner_id', metavar='POSITIONER', type=int)
@click.argument('alpha', metavar='ALPHA', type=float)
@click.argument('beta', metavar='BETA', type=float)
@pass_fps
@cli_coro
async def set_positions(obj, positioner_id, alpha, beta):
    """Sets the position of the alpha and beta arms."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError('alpha must be in the range [0, 360)')

    if beta < 0 or beta >= 360:
        raise click.UsageError('beta must be in the range [0, 360)')

    async with obj as fps:

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
@pass_fps
@cli_coro
async def demo(obj, positioner_id, alpha=None, beta=None, speed=None, moves=None,
               skip_errors=False):
    """Moves a robot to random positions."""

    if (alpha[0] >= alpha[1]) or (alpha[0] < 0 or alpha[1] > 360):
        raise click.UsageError('alpha must be in the range [0, 360)')

    if (beta[0] >= beta[1]) or (beta[0] < 0 or beta[1] > 360):
        raise click.UsageError('beta must be in the range [0, 360)')

    if (speed[0] >= speed[1]) or (speed[0] < 0 or speed[1] >= 3000):
        raise click.UsageError('speed must be in the range [0, 3000)')

    async with obj as fps:

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
                                           speed=(alpha_speed, beta_speed))

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
@pass_fps
@cli_coro
async def home(obj, positioner_id):
    """Initialise datums."""

    async with obj as fps:

        if positioner_id is None:
            positioners = fps.positioners.values()
        else:
            positioners = [fps.positioners[positioner_id]]

        valid_positioners = [positioner for positioner in positioners
                             if positioner.status.initialised]

        if len(valid_positioners) < len(positioners):
            warnings.warn(f'{len(positioners) - len(valid_positioners)} positioners '
                          'have not been initialised and will not be homed.')

        await asyncio.gather(*[fps.send_command('INITIALIZE_DATUMS',
                                                positioner_id=pos.positioner_id)
                               for pos in valid_positioners])

    return
