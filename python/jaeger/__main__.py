#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-12
# @Filename: cli.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
import warnings
from functools import wraps

from typing import Any, Optional

import click
import numpy
from click_default_group import DefaultGroup

from sdsstools.daemonizer import DaemonGroup

from jaeger import can_log, config, log
from jaeger.commands.bootloader import load_firmware
from jaeger.commands.calibration import calibrate_positioner
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.fps import FPS, LOCK_FILE
from jaeger.positioner import Positioner
from jaeger.testing import VirtualFPS


__FPS__ = None


def shutdown(sign):
    """Shuts down the FPS and stops the positioners in case of a signal interrupt."""

    if __FPS__ is not None:
        __FPS__.send_command("SEND_TRAJECTORY_ABORT", positioner_ids=None, now=True)
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

    def __init__(
        self,
        profile,
        ieb=None,
        initialise=True,
        npositioners=10,
        enable_low_temperature=True,
        use_lock=True,
    ):

        self.profile = profile
        if self.profile in ["test", "virtual"]:
            self.profile = "virtual"

        self.ieb = ieb
        self.enable_low_temperature = enable_low_temperature
        self.initialise = initialise
        self.use_lock = use_lock

        self.vpositioners = []
        self.npositioners = npositioners

        self.fps = None

    async def __aenter__(self):

        global __FPS__

        # If profile is test we start a VirtualFPS first so that it can respond
        # to the FPS class.
        if self.profile == "virtual":
            self.fps = VirtualFPS()
            for pid in range(self.npositioners):
                self.fps.add_virtual_positioner(pid + 1)
        else:
            self.fps = FPS(can=self.profile, ieb=self.ieb)

        __FPS__ = self.fps

        if self.initialise:
            await self.fps.initialise(
                enable_low_temperature=self.enable_low_temperature,
                use_lock=self.use_lock,
            )
            if self.enable_low_temperature is False:
                warnings.warn(
                    "Disabling low temperature handling for sextant.",
                    JaegerUserWarning,
                )

        return self.fps

    async def __aexit__(self, *excinfo):
        try:
            if self.fps is None:
                return
            await self.fps.shutdown()
        except JaegerError as err:
            warnings.warn(f"Failed shutting down FPS: {err}", JaegerUserWarning)


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
    "--virtual",
    is_flag=True,
    help="Runs a virtual FPS with virtual positioners. Same as --profile=virtual.",
)
@click.option(
    "-n",
    "--npositioners",
    type=int,
    default=10,
    help="How many virtual positioners must be connected to the virtual FPS.",
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
    "-s",
    "--sextant",
    is_flag=True,
    help="Use engineering sextant instead of IEB. "
    "Modifies the internal configuration file.",
)
@click.option(
    "--allow-host",
    is_flag=True,
    help="Allows running jager in a host other than sdss5-fps.",
)
@click.option(
    "--no-lock",
    is_flag=True,
    help="Do not use the lock file, or ignore it if present.",
)
@click.pass_context
def jaeger(
    ctx,
    config_file,
    profile,
    verbose,
    quiet,
    ieb,
    sextant,
    virtual,
    npositioners,
    allow_host,
    no_lock,
):
    """CLI for the SDSS-V focal plane system.

    If called without subcommand starts the actor.

    """

    if allow_host is False:
        hostname = socket.getfqdn()
        if hostname.endswith("apo.nmsu.edu") or hostname.endswith("lco.cl"):
            if not hostname.startswith("sdss5-fps"):
                raise RuntimeError("At the observatories jaeger must run on sdss5-fps.")

    if verbose > 0 and quiet:
        raise click.UsageError("--quiet and --verbose are mutually exclusive.")

    if config_file:
        config.load(config_file)

    actor_config = config.get("actor", {})

    if verbose == 1:
        log.sh.setLevel(logging.INFO)
        actor_config["verbose"] = logging.INFO
    elif verbose == 2:
        log.sh.setLevel(logging.DEBUG)
        actor_config["verbose"] = logging.DEBUG
    elif verbose >= 3:
        log.sh.setLevel(logging.DEBUG)
        can_log.sh.setLevel(logging.DEBUG)
        actor_config["verbose"] = logging.DEBUG

    if quiet:
        log.handlers.remove(log.sh)
        warnings.simplefilter("ignore")
        actor_config["verbose"] = 100

    if sextant:
        sextant_file = os.path.join(os.path.dirname(__file__), "etc/sextant.yaml")
        config["files"]["ieb_config"] = sextant_file
        log.debug(f"Using internal IEB sextant onfiguration file {sextant_file}.")

    if sextant or "sextants/" in config["files"]["ieb_config"]:
        enable_low_temperature = False
    else:
        enable_low_temperature = True

    if virtual is True:
        profile = "virtual"

    ctx.obj = FPSWrapper(
        profile,
        ieb=ieb,
        npositioners=npositioners,
        enable_low_temperature=enable_low_temperature,
        use_lock=not no_lock,
    )


LOG_FILE = os.path.join(
    os.environ.get("ACTOR_DAEMON_LOG_DIR", "$HOME/logs"),
    "jaeger/jaeger.log",
)


@jaeger.group(
    cls=DaemonGroup,
    prog="jaeger_actor",
    workdir=os.getcwd(),
    log_file=LOG_FILE,
)
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
@click.argument(
    "SEXTANTS",
    type=int,
    nargs=-1,
    required=False,
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Do not ask for confirmation.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    help="Forces skipping of invalid positioners",
)
@click.option(
    "-p",
    "--positioners",
    type=str,
    help="Comma-separated positioners to upgrade",
)
@click.option(
    "-n",
    "--no-cycle",
    is_flag=True,
    help="Does not power cycle positioners before upgrading each sextant",
)
@click.option(
    "-o",
    "--all-on",
    is_flag=True,
    help="Powers on all sextants after a successful upgrade.",
)
@pass_fps
@cli_coro
async def upgrade_firmware(
    fps_maker,
    firmware_file,
    force,
    positioners,
    no_cycle,
    sextants,
    all_on,
    yes,
):
    """Upgrades the firmaware."""

    if positioners is not None:
        positioners = [int(positioner.strip()) for positioner in positioners.split(",")]

    fps_maker.initialise = False

    sextants = sextants or (1, 2, 3, 4, 5, 6)

    if not yes:
        click.confirm(
            f"Upgrade firmware for sextant(s) {', '.join(map(str, sextants))}?",
            default=False,
            abort=True,
        )

    async with fps_maker as fps:

        ps_devs = []
        if fps.ieb and no_cycle is False:
            for module in fps.ieb.modules.values():
                for dev_name in module.devices:
                    if dev_name.upper().startswith("PS"):
                        ps_devs.append(dev_name)

        for sextant in sextants:
            log.info(f"Upgrading firmware on sextant {sextant}.")

            if fps.ieb and no_cycle is False:
                log.info("Power cycling positioners")

                for dev in ps_devs:
                    await fps.ieb.get_device(dev).open()
                    await asyncio.sleep(1)

                await asyncio.sleep(5)

                dev = "PS" + str(sextant)
                await fps.ieb.get_device(dev).close()

                await asyncio.sleep(3)

            await fps.initialise(start_pollers=False)

            await load_firmware(
                fps,
                firmware_file,
                positioners=positioners,
                force=force,
                show_progressbar=True,
            )

    if all_on is True:
        log.info("Powering on sextants.")
        for sextant in sextants:
            await fps.ieb.get_device(f"PS{sextant}").close()
            await asyncio.sleep(3)


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


@jaeger.command()
@click.argument("positioner_id", metavar="POSITIONER", type=int)
@pass_fps
@cli_coro
async def status(fps_maker: FPSWrapper, positioner_id: int):
    """Returns the status of a positioner with low-level initialisation."""

    fps_maker.initialise = False

    async with fps_maker as fps:

        pos = Positioner(positioner_id, fps)

        try:
            await pos.update_firmware_version()
            print(f"Firmware: {pos.firmware}")
            print(f"Bootloader: {pos.is_bootloader()}")
        except Exception as err:
            raise JaegerError(f"Failed retrieving firmware: {err}")

        try:
            await pos.update_status()
            bit_names = ", ".join(bit.name for bit in pos.status.active_bits)
            print(f"Status: {pos.status.value} ({bit_names})")
        except Exception as err:
            raise JaegerError(f"Failed retrieving status: {err}")

        try:
            await pos.update_position()
            print(f"Position: {pos.position}")
        except Exception as err:
            raise JaegerError(f"Failed retrieving position: {err}")


@jaeger.command()
@click.option(
    "--collision-buffer",
    type=click.FloatRange(1.6, 3.0),
    help="Custom collision buffer",
)
@click.option(
    "--force",
    is_flag=True,
    help="Execute unwind even in presence of deadlocks.",
)
@pass_fps
@cli_coro
async def unwind(fps_maker, collision_buffer: float | None = None, force: bool = False):
    """Unwinds the array."""

    from jaeger.kaiju import unwind

    async with fps_maker as fps:

        await fps.update_position()
        positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.values()}

        log.info("Calculating trajectory.")
        trajectory = await unwind(
            positions,
            collision_buffer=collision_buffer,
            force=force,
        )

        log.info("Executing unwind trajectory.")
        await fps.send_trajectory(trajectory)

    return


@jaeger.command()
@click.argument("EXPLODE-DEG", type=float)
@pass_fps
@cli_coro
async def explode(fps_maker, explode_deg: float):
    """Explodes the array."""

    from jaeger.kaiju import explode

    async with fps_maker as fps:

        await fps.update_position()
        positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.values()}

        log.info("Calculating trajectory.")
        trajectory = await explode(positions, explode_deg=explode_deg)

        log.info("Executing explode trajectory.")
        await fps.send_trajectory(trajectory)

    return


@jaeger.command()
@click.argument("PATH", required=False, type=click.Path(exists=False, dir_okay=False))
@click.option("--collision-buffer", type=float, help="The collision buffer.")
@pass_fps
@cli_coro
async def snapshot(
    fps_maker: FPSWrapper,
    path: Optional[str] = None,
    collision_buffer: float | None = None,
):
    """Takes a snapshot image."""

    if path is not None:
        path = str(path)

    async with fps_maker as fps:

        await fps.update_position()
        filename = await fps.save_snapshot(path, collision_buffer=collision_buffer)

    print(f"Snapshot saved to {filename}")


@jaeger.command()
@pass_fps
@cli_coro
async def unlock(fps_maker: FPSWrapper):
    """Unlocks the FPS."""

    warnings.filterwarnings(
        "ignore",
        message=".+FPS was collided and has been locked.+",
        category=JaegerUserWarning,
    )

    async with fps_maker as fps:
        await fps.unlock()

    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)


if __name__ == "__main__":
    jaeger()
