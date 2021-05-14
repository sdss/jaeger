#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: cli.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import asyncio
import logging
import os
import signal
import sys
import warnings
from functools import wraps

from typing import Any

import click
import numpy
from click_default_group import DefaultGroup

from sdsstools.daemonizer import DaemonGroup

from jaeger import can_log, config, log
from jaeger.commands.bootloader import load_firmware
from jaeger.commands.calibration import calibrate_positioner
from jaeger.fps import FPS
from jaeger.testing import VirtualFPS


__FPS__ = None


def shutdown(sign):
    """Shuts down the FPS and stops the positioners in case of a signal interrupt."""

    if __FPS__ is not None:
        __FPS__.send_command("STOP_TRAJECTORY", positioner_id=0, synchronous=True)
        log.error(f"stopping positioners and cancelling due to {sign.name}")
        sys.exit(0)
    else:
        log.error(f"cannot shutdown FPS before {sign.name}")
        sys.exit(1)


def cli_coro(f):
    """Decorator function that allows defining coroutines with click."""

    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        for ss in signals:
            loop.add_signal_handler(ss, shutdown, ss)
        return loop.run_until_complete(f(*args, **kwargs))

    return wrapper


class FPSWrapper(object):
    """A helper to store FPS initialisation parameters."""

    def __init__(self, profile, layout, ieb=None, danger=None, initialise=True):

        self.profile = profile
        if self.profile in ["test", "virtual"]:
            self.profile = "virtual"

        self.layout = layout
        self.ieb = ieb
        self.danger = danger
        self.initialise = initialise

        self.fps = None

    async def __aenter__(self):

        global __FPS__

        # If profile is test we start a VirtualFPS first so that it can respond
        # to the FPS class.
        if self.profile == "virtual":
            self.fps = VirtualFPS(layout=self.layout)
        else:
            self.fps = FPS(
                can_profile=self.profile,
                layout=self.layout,
                ieb=self.ieb,
                engineering_mode=self.danger,
            )

        __FPS__ = self.fps

        if self.initialise:
            await self.fps.initialise()

        return self.fps

    async def __aexit__(self, *excinfo):
        await self.fps.shutdown()


pass_fps = click.make_pass_decorator(FPSWrapper, ensure=True)


@click.group(cls=DefaultGroup, default="actor", default_if_no_args=True)
@click.option(
    "-c",
    "--config",
    "config_file",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to the user configuration file.",
)
@click.option(
    "-p",
    "--profile",
    type=str,
    help="The bus interface profile.",
)
@click.option(
    "-l",
    "--layout",
    type=str,
    help="The FPS layout.",
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Debug mode. Use additional v for more details.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Disable all console logging.",
)
@click.option(
    "--ieb/--no-ieb",
    default=None,
    help="Does not connect to the IEB.",
)
@click.option(
    "--danger",
    is_flag=True,
    help="Enables engineering mode. Most safety checks will be disabled.",
)
@click.option(
    "-s",
    "--sextant",
    is_flag=True,
    help="Use engineering sextant instead of IEB. "
    "Modifies the internal configuration file.",
)
@click.pass_context
def jaeger(ctx, config_file, layout, profile, verbose, quiet, ieb, danger, sextant):
    """CLI for the SDSS-V focal plane system.

    If called without subcommand starts the actor.

    """

    if verbose > 0 and quiet:
        raise click.UsageError("--quiet and --verbose are mutually exclusive.")

    if verbose == 1:
        log.sh.setLevel(logging.INFO)
    elif verbose == 2:
        log.sh.setLevel(logging.DEBUG)
    elif verbose >= 3:
        log.sh.setLevel(logging.DEBUG)
        can_log.sh.setLevel(logging.DEBUG)

    if quiet:
        log.sh.propagate = False  # type: ignore
        warnings.simplefilter("ignore")

    if config_file:
        config.load(config_file)

    if sextant:
        sextant_file = os.path.join(os.path.dirname(__file__), "etc/sextant.yaml")
        config["files"]["ieb_config"] = sextant_file
        log.debug(f"Using internal IEB sextant onfiguration file {sextant_file}.")

    ctx.obj = FPSWrapper(profile, layout, ieb, danger)


@jaeger.group(cls=DaemonGroup, prog="jaeger_actor", workdir=os.getcwd())
@click.option(
    "--no-tron",
    is_flag=True,
    help="Does not connect to Tron.",
)
@pass_fps
@cli_coro
async def actor(fps_maker, no_tron):
    """Runs the actor."""

    try:
        from jaeger.actor import JaegerActor
    except ImportError:
        raise ImportError("CLU needs to be installed to run jaeger as an actor.")

    actor_config = config["actor"].copy()
    actor_config.pop("status", None)

    if no_tron:
        actor_config.pop("tron", None)

    async with fps_maker as fps:
        actor_: Any = await JaegerActor.from_config(actor_config, fps).start()
        await actor_.start_status_server(
            config["actor"]["status"]["port"],
            delay=config["actor"]["status"]["delay"],
        )
        await actor_.run_forever()


@jaeger.command(name="upgrade-firmware")
@click.argument(
    "firmware-file",
    nargs=1,
    type=click.Path(exists=True),
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Forces skipping of invalid positioners",
)
@click.option(
    "-s",
    "--positioners",
    type=str,
    help="Comma-separated positioners to upgrade",
)
@click.option(
    "-c",
    "--cycle",
    is_flag=True,
    help="Power cycle positioners before upgrade to set them to bootloader mode. "
    "If --sextant not used, cycles all sextants",
)
@click.option(
    "--sextant",
    type=int,
    help="The sextant to power cycle",
)
@pass_fps
@cli_coro
async def upgrade_firmware(
    fps_maker,
    firmware_file,
    force,
    positioners,
    cycle,
    sextant,
):
    """Upgrades the firmaware."""

    if positioners is not None:
        positioners = [int(positioner.strip()) for positioner in positioners.split(",")]

    async with fps_maker as fps:

        if fps.ieb and cycle:
            log.info("power cycling positioners")
            if sextant:
                devs = [f"PS{sextant}"]
            else:
                devs = [f"PS{s}" for s in range(1, 7)]

            await asyncio.gather(*[fps.ieb.get_device(dev).open() for dev in devs])
            await asyncio.sleep(5)
            await asyncio.gather(*[fps.ieb.get_device(dev).close() for dev in devs])
            await asyncio.sleep(3)
            await fps.initialise()

        await load_firmware(
            fps,
            firmware_file,
            positioners=positioners,
            force=force,
            show_progressbar=True,
        )


@jaeger.command()
@click.argument("positioner-id", nargs=1, type=int)
@click.option(
    "--motors/--no-motors",
    is_flag=True,
    default=True,
    help="Run the motor calibration.",
)
@click.option(
    "--datums/--no-datums",
    is_flag=True,
    default=True,
    help="Run the datum calibration.",
)
@click.option(
    "--cogging/--no-cogging",
    is_flag=True,
    default=True,
    help="Run the cogging calibration (can take a long time).",
)
@pass_fps
@cli_coro
async def calibrate(fps_maker, positioner_id, motors, datums, cogging):
    """Runs a full calibration on a positioner."""

    fps_maker.initialise = False
    fps_maker.danger = True

    async with fps_maker as fps:
        await fps.initialise(start_pollers=False)
        await calibrate_positioner(
            fps,
            positioner_id,
            motors=motors,
            datums=datums,
            cogging=cogging,
        )


@jaeger.command()
@click.argument("positioner_id", metavar="POSITIONER", type=int)
@click.argument("alpha", metavar="ALPHA", type=float)
@click.argument("beta", metavar="BETA", type=float)
@click.option(
    "--speed",
    type=(float, float),
    default=(None, None),
    help="The speed for the alpha and beta motors.",
    show_default=True,
)
@pass_fps
@cli_coro
async def goto(fps_maker, positioner_id, alpha, beta, speed=None):
    """Moves a robot to a given position."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError("alpha must be in the range [0, 360)")

    if beta < 0 or beta >= 360:
        raise click.UsageError("beta must be in the range [0, 360)")

    if speed[0] or speed[1]:
        if speed[0] < 0 or speed[0] >= 3000 or speed[1] < 0 or speed[1] >= 3000:
            raise click.UsageError("speed must be in the range [0, 3000)")

    async with fps_maker as fps:

        positioner = fps.positioners[positioner_id]
        result = await positioner.initialise()
        if not result:
            log.error("positioner is not connected or failed to initialise.")
            return

        await positioner.goto(alpha=alpha, beta=beta, speed=(speed[0], speed[1]))

    return


@jaeger.command(name="set-positions")
@click.argument("positioner_id", metavar="POSITIONER", type=int)
@click.argument("alpha", metavar="ALPHA", type=float)
@click.argument("beta", metavar="BETA", type=float)
@pass_fps
@cli_coro
async def set_positions(fps_maker, positioner_id, alpha, beta):
    """Sets the position of the alpha and beta arms."""

    if alpha < 0 or alpha >= 360:
        raise click.UsageError("alpha must be in the range [0, 360)")

    if beta < 0 or beta >= 360:
        raise click.UsageError("beta must be in the range [0, 360)")

    async with fps_maker as fps:

        positioner = fps.positioners[positioner_id]

        result = await positioner.set_position(alpha, beta)

        if not result:
            log.error("failed to set positions.")
            return

        log.info(f"positioner {positioner_id} set to {(alpha, beta)}.")


@jaeger.command()
@click.argument("positioner_id", metavar="POSITIONER", type=int)
@click.option(
    "-n",
    "--moves",
    type=int,
    help="Number of moves to perform. Otherwise runs forever.",
)
@click.option(
    "--alpha",
    type=(int, int),
    default=(0, 360),
    help="Range of alpha positions.",
    show_default=True,
)
@click.option(
    "--beta",
    type=(int, int),
    default=(0, 180),
    help="Range of beta positions.",
    show_default=True,
)
@click.option(
    "--speed",
    type=(int, int),
    default=(500, 1500),
    help="Range of speed.",
    show_default=True,
)
@click.option(
    "-f",
    "--skip-errors",
    is_flag=True,
    help="If an error occurs, ignores it and commands another move.",
)
@pass_fps
@cli_coro
async def demo(
    fps_maker,
    positioner_id,
    alpha=None,
    beta=None,
    speed=None,
    moves=None,
    skip_errors=False,
):
    """Moves a robot to random positions."""

    if (alpha[0] >= alpha[1]) or (alpha[0] < 0 or alpha[1] > 360):
        raise click.UsageError("alpha must be in the range [0, 360)")

    if (beta[0] >= beta[1]) or (beta[0] < 0 or beta[1] > 360):
        raise click.UsageError("beta must be in the range [0, 360)")

    if (speed[0] >= speed[1]) or (speed[0] < 0 or speed[1] >= 3000):
        raise click.UsageError("speed must be in the range [0, 3000)")

    async with fps_maker as fps:

        positioner = fps.positioners[positioner_id]
        result = await positioner.initialise()
        if not result:
            log.error("positioner is not connected or failed to initialise.")
            return

        done_moves = 0
        while True:

            alpha_move = numpy.random.randint(low=alpha[0], high=alpha[1])
            beta_move = numpy.random.randint(low=beta[0], high=beta[1])
            alpha_speed = numpy.random.randint(low=speed[0], high=speed[1])
            beta_speed = numpy.random.randint(low=speed[0], high=speed[1])

            warnings.warn(f"running step {done_moves+1}")

            result = await positioner.goto(
                alpha=alpha_move, beta=beta_move, speed=(alpha_speed, beta_speed)
            )

            if result is False:
                if skip_errors is False:
                    return
                else:
                    warnings.warn(
                        "an error happened but ignoring it because skip-error=True"
                    )
                    continue

            done_moves += 1

            if moves is not None and done_moves == moves:
                return


@jaeger.command()
@click.argument("positioner_id", metavar="POSITIONER", type=int, required=False)
@pass_fps
@cli_coro
async def home(fps_maker, positioner_id):
    """Initialise datums."""

    async with fps_maker as fps:

        if positioner_id is None:
            positioners = fps.positioners.values()
        else:
            positioners = [fps.positioners[positioner_id]]

        valid_positioners = [
            positioner for positioner in positioners if positioner.status.initialised
        ]

        await asyncio.gather(*[positioner.home() for positioner in valid_positioners])

    return
