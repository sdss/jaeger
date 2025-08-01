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
from copy import deepcopy
from functools import wraps

from typing import Optional

import click
from click_default_group import DefaultGroup

from sdsstools.daemonizer import DaemonGroup

from jaeger import can_log, config, log
from jaeger.commands.bootloader import load_firmware
from jaeger.commands.calibration import calibrate_positioners
from jaeger.commands.goto import goto as goto_
from jaeger.exceptions import (
    FPSLockedError,
    JaegerError,
    JaegerUserWarning,
    TrajectoryError,
)
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
        skip_assignments_check=False,
    ):
        self.profile = profile
        if self.profile in ["test", "virtual"]:
            self.profile = "virtual"

        self.ieb = ieb
        self.skip_assignments_check = skip_assignments_check
        self.initialise = initialise

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
                skip_assignments_check=self.skip_assignments_check,
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
    help="Allows running jaeger in a host other than sdss5-fps.",
)
@click.option(
    "--no-lock",
    is_flag=True,
    help="Do not use the lock file, or ignore it if present.",
)
@click.option(
    "-x",
    "--skip-assignments-check",
    is_flag=True,
    help="Do not fail if the fibre assignment check fails.",
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
    skip_assignments_check,
):
    """CLI for the SDSS-V focal plane system.

    If called without subcommand starts the actor.

    """

    if allow_host is False:
        hostname = socket.getfqdn()
        if hostname.endswith("apo.nmsu.edu") or hostname.endswith("lco.cl"):
            if not hostname.startswith("sdss5-fps"):
                raise RuntimeError(
                    "At the observatories jaeger must run on sdss5-fps. "
                    "If you want to run jaeger on another computer use --allow-host."
                )

    if verbose > 0 and quiet:
        raise click.UsageError("--quiet and --verbose are mutually exclusive.")

    if config_file:
        config.load(config_file)

    if no_lock:
        config["fps"]["use_lock"] = False

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
        config["ieb"]["config"] = sextant_file
        log.debug(f"Using internal IEB sextant onfiguration file {sextant_file}.")

    if virtual is True:
        profile = "virtual"

    ctx.obj = FPSWrapper(
        profile,
        ieb=ieb,
        npositioners=npositioners,
        skip_assignments_check=skip_assignments_check,
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
@click.option(
    "--no-chiller",
    is_flag=True,
    help="Does not start the chiller bot.",
)
@click.option(
    "--no-alerts",
    is_flag=True,
    help="Does not start the alerts bot.",
)
@pass_fps
@cli_coro
async def actor(
    fps_maker,
    no_tron: bool = False,
    no_chiller: bool = False,
    no_alerts: bool = False,
):
    """Runs the actor."""

    try:
        from jaeger.actor import JaegerActor
    except ImportError:
        raise ImportError("CLU needs to be installed to run jaeger as an actor.")

    config_copy = deepcopy(config)
    if "actor" not in config_copy:
        raise RuntimeError("Configuration file does not contain an actor section.")

    config_copy["actor"].pop("status", None)

    if no_tron:
        config_copy["actor"].pop("tron", None)

    # Do not initialise FPS so that we can define the actor instance first.
    fps_maker.initialise = False

    async with fps_maker as fps:
        actor_: JaegerActor = JaegerActor.from_config(config_copy, fps)

        await fps.initialise(skip_assignments_check=fps_maker.skip_assignments_check)

        await actor_.start(chiller=not no_chiller, alerts=not no_alerts)
        await actor_.run_forever()

        await actor_.stop()


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
        await calibrate_positioners(
            fps,
            "both",
            positioner_id,
            motors=motors,
            datums=datums,
            cogging=cogging,
        )


@jaeger.command()
@click.argument("POSITIONER-IDS", type=int, nargs=-1)
@click.argument("ALPHA", type=click.FloatRange(-10.0, 370.0))
@click.argument("BETA", type=click.FloatRange(-10.0, 370.0))
@click.option(
    "-r",
    "--relative",
    is_flag=True,
    help="Whether this is a relative move",
)
@click.option(
    "-s",
    "--speed",
    type=click.FloatRange(100.0, 4000.0),
    help="The speed for both alpha and beta arms, in RPS on the input.",
)
@click.option(
    "-a",
    "--all",
    is_flag=True,
    default=False,
    help="Applies to all valid positioners.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="Forces a move to happen.",
)
@click.option(
    "--go-cowboy",
    is_flag=True,
    help="If set, does not use kaiju-validated trajectories.",
)
@click.option(
    "--use-sync/-no-use-sync",
    " /-S",
    default=True,
    help="Whether to use the SYNC line to start the trajectory.",
)
@pass_fps
@cli_coro
async def goto(
    fps_maker,
    positioner_ids: tuple[int, ...] | list[int],
    alpha: float,
    beta: float,
    speed: float | None,
    all: bool = False,
    force: bool = False,
    relative: bool = False,
    use_sync: bool = True,
    go_cowboy: bool = False,
):
    """Sends positioners to a given (alpha, beta) position."""

    with fps_maker as fps:
        if all:
            if not force:
                raise JaegerError("Use --force to move all positioners at once.")
            positioner_ids = list(fps.positioners.keys())
        else:
            positioner_ids = list(positioner_ids)

        if not relative:
            if alpha < 0 or beta < 0:
                raise JaegerError("Negative angles only allowed in relative mode.")

        new_positions = {}
        for pid in positioner_ids:
            new_positions[pid] = (alpha, beta)

        try:
            await goto_(
                fps,
                new_positions,
                speed=speed,
                relative=relative,
                use_sync_line=use_sync,
                go_cowboy=go_cowboy,
            )
        except (JaegerError, TrajectoryError) as err:
            raise JaegerError(f"Goto command failed: {err}")


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
    "--axis",
    type=click.Choice(["alpha", "beta"], case_sensitive=True),
    help="The axis to home. If not set, homes both axes at the same time.",
)
@pass_fps
@cli_coro
async def home(fps_maker: FPSWrapper, positioner_id: int, axis: str | None = None):
    """Home a single positioner, sending a GO_TO_DATUMS command."""

    alpha: bool = axis == "alpha" or axis is None
    beta: bool = axis == "beta" or axis is None

    async with fps_maker as fps:
        if positioner_id not in fps or fps[positioner_id].initialised is False:
            raise ValueError("Positioner is not connected.")
        if fps[positioner_id].disabled or fps[positioner_id].offline:
            raise ValueError("Positioner has been disabled.")

        await fps[positioner_id].home(alpha=alpha, beta=beta)

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
        if fps.locked:
            FPSLockedError("The FPS is locked.")

        await fps.update_position()
        positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.values()}

        log.info("Calculating trajectory.")
        trajectory = await unwind(
            positions,
            collision_buffer=collision_buffer,
            disabled=[pid for pid in fps.positioners if fps.positioners[pid].disabled],
            force=force,
        )

        log.info("Executing unwind trajectory.")
        await fps.send_trajectory(trajectory)

    return


@jaeger.command()
@click.argument("EXPLODE-DEG", type=float)
@click.option("--one", type=int, help="Only explode this positioner.")
@pass_fps
@cli_coro
async def explode(
    fps_maker,
    explode_deg: float,
    one: int | None = None,
):
    """Explodes the array."""

    from jaeger.kaiju import explode

    async with fps_maker as fps:
        if fps.locked:
            FPSLockedError("The FPS is locked.")

        await fps.update_position()
        positions = {p.positioner_id: (p.alpha, p.beta) for p in fps.values()}

        log.info("Calculating trajectory.")
        trajectory = await explode(
            positions,
            explode_deg=explode_deg,
            disabled=[pid for pid in fps.positioners if fps.positioners[pid].disabled],
            positioner_id=one,
        )

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
