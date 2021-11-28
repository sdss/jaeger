#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-05-13
# @Filename: commands.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import asyncio
import pathlib

from typing import TYPE_CHECKING, List, Tuple

import click
import numpy

import clu

from jaeger import config
from jaeger.commands import CommandID, SetCurrent, Trajectory
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.utils import get_goto_move_time

from . import jaeger_parser


if TYPE_CHECKING:
    from clu import Command

    from jaeger import FPS
    from jaeger.actor import JaegerActor


__all__ = [
    "goto",
    "speed",
    "initialise",
    "stop",
    "hall",
    "trajectory",
    "unlock",
    "current",
    "status",
]


def check_positioners(positioner_ids, command, fps, initialised=False):
    """Checks if some of the positioners are not connected."""

    if any([pid not in fps.positioners for pid in positioner_ids]):
        command.fail(error="some positioners are not connected.")
        return False

    if initialised:
        if any([not fps[pid].initialised for pid in positioner_ids]):
            command.fail(error="some positioners are not initialised.")
            return False

    return True


@jaeger_parser.command()
@click.argument("POSITIONER-IDS", type=int, nargs=-1)
@click.argument("ALPHA", type=click.FloatRange(-360.0, 360.0))
@click.argument("BETA", type=click.FloatRange(-360.0, 360.0))
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
    nargs=2,
    help="The speed of both alpha and beta arms, in RPS on the input.",
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
    "--use-sync/-no-use-sync",
    " /-S",
    default=True,
    help="Whether to use the SYNC line to start the trajectory.",
)
async def goto(
    command: Command[JaegerActor],
    fps: FPS,
    positioner_ids: Tuple[int, ...],
    alpha: float,
    beta: float,
    speed: Tuple[float, float],
    all: bool = False,
    force: bool = False,
    relative: bool = False,
    use_sync: bool = True,
):
    """Sends positioners to a given (alpha, beta) position."""

    assert command.actor

    if all:
        if not force:
            return command.fail(error="Use --force to move all positioners at once.")
        pids: List[int] = list(fps.positioners.keys())
    else:
        pids = list(positioner_ids)

    if not relative:
        if alpha < 0 or beta < 0:
            return command.fail(error="Negative angles only allowed in relative mode.")

    if not check_positioners(pids, command, fps, initialised=True):
        return

    if fps.moving:
        return command.fail(error="FPS is moving. Cannot send goto.")

    if speed is None:
        default_speed = config["positioner"]["motor_speed"]
        speed = (default_speed, default_speed)

    max_time = 0.0

    for pid in pids:

        # Manually calculate the max move time we'll encounter.
        p_alpha, p_beta = fps[pid].position

        if p_alpha is None or p_beta is None:
            return command.fail(error="Some positioners do not know their positions.")

        delta_alpha = abs(p_alpha - alpha) if not relative else alpha
        delta_beta = abs(p_beta - beta) if not relative else beta

        time_alpha = get_goto_move_time(
            delta_alpha,
            speed=speed[0] or fps[pid].speed[0],
        )
        time_beta = get_goto_move_time(
            delta_beta,
            speed=speed[1] or fps[pid].speed[1],
        )

        if time_alpha > max_time:
            max_time = time_alpha
        if time_beta > max_time:
            max_time = time_beta

    command.info(move_time=round(max_time, 2))

    try:
        await command.actor.fps.goto(
            pids,
            alpha,
            beta,
            speed=speed,
            relative=relative,
            use_sync_line=use_sync,
        )
    except JaegerError as err:
        return command.fail(error=f"Goto command failed: {err}")

    command.finish(text="Destination reached")


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int, nargs=-1)
@click.argument("ALPHA", type=click.FloatRange(50.0, 5000.0))
@click.argument("BETA", type=click.FloatRange(50.0, 5000.0))
@click.option(
    "-a",
    "--all",
    is_flag=True,
    default=False,
    help="applies to all valid positioners.",
)
async def speed(command, fps, positioner_id, alpha, beta, all):
    """Sets the ``(alpha, beta)`` speed in RPM on the input."""

    if all:
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=True):
        return

    if fps.moving:
        return command.fail(error="FPS is moving. Cannot send set_speed.")

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].set_speed(alpha, beta))

    result = await clu.as_complete_failer(tasks, on_fail_callback=fps.stop_trajectory)

    if not result[0]:
        error_message = result[1] or "set speed command failed"
        command.set_status(clu.CommandStatus.FAILED, error=error_message)
    else:
        command.set_status(clu.CommandStatus.DONE, text="Set speed done")


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int, nargs=-1)
@click.option(
    "--datums",
    is_flag=True,
    help="If set, initialises the datums.",
)
async def initialise(command, fps, positioner_id, datums=False):
    """Initialises positioners."""

    if not check_positioners(positioner_id, command, fps):
        return

    tasks = []
    for pid in positioner_id:
        tasks.append(fps.positioners[pid].initialise(initialise_datums=datums))

    result = await clu.as_complete_failer(tasks)

    if not result[0]:
        error_message = result[1] or "initialise failed"
        command.set_status(clu.CommandStatus.FAILED, error=error_message)
    else:
        command.set_status(clu.CommandStatus.DONE, text="Initialisation complete")


@jaeger_parser.command()
@click.argument("POSITIONER-IDS", type=int, nargs=-1, required=False)
@click.option(
    "-f",
    "--full",
    is_flag=True,
    default=False,
    help="outputs more statuses.",
)
async def status(command, fps, positioner_ids, full):
    """Reports the position and status bit of a list of positioners."""

    positioner_ids = positioner_ids or list(fps.positioners.keys())

    if not check_positioners(positioner_ids, command, fps):
        return

    try:
        await fps.update_status(positioner_ids=0)
        await fps.update_position(positioner_ids=positioner_ids)
    except JaegerError as err:
        return command.fail(error=f"Failed reporting status: {err}")

    n_trajs = (
        await fps.send_command(
            CommandID.GET_NUMBER_TRAJECTORIES,
            positioner_ids=positioner_ids,
        )
    ).get_replies()

    command.info(locked=fps.locked)

    command.info(n_positioners=len(fps.positioners))

    for pid in sorted(positioner_ids):
        p = fps[pid]

        alpha_pos = -999 if p.alpha is None else numpy.round(p.alpha, 4)
        beta_pos = -999 if p.beta is None else numpy.round(p.beta, 4)

        n_trajs_pid = n_trajs[pid] if n_trajs[pid] is not None else "?"

        if pid in fps.positioner_to_bus:
            interface, bus = fps.positioner_to_bus[pid]
            interface = fps.can.interfaces.index(interface) + 1
        else:
            interface = -1
            bus = -1

        command.write(
            "i",
            positioner_status=[
                p.positioner_id,
                alpha_pos,
                beta_pos,
                f"0x{int(p.status):x}",
                p.initialised,
                p.is_bootloader() or False,
                p.firmware or "?",
                interface,
                bus,
                n_trajs_pid,
            ],
        )

    command.info(fps_status=f"0x{fps.status.value:x}")

    if full:
        await clu.Command("ieb status", parent=command).parse()

    command.set_status(clu.CommandStatus.DONE)


@jaeger_parser.command()
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
@click.argument("ALPHA", type=click.FloatRange(0.0, 100.0))
@click.argument("BETA", type=click.FloatRange(0.0, 100.0))
@click.option(
    "-a",
    "--all",
    is_flag=True,
    default=False,
    help="applies to all connected positioners.",
)
async def current(command, fps, positioner_id, alpha, beta, all):
    """Sets the current of the positioner."""

    if all:
        positioner_id = [pid for pid in fps.positioners if fps[pid].initialised]

    if len(positioner_id) == 0:
        return command.fail(error="no positioners provided.")

    if not check_positioners(positioner_id, command, fps):
        return

    if fps.moving:
        return command.fail(error="FPS is moving. Cannot send set current.")

    commands = [
        fps.send_command(SetCurrent(pid, alpha=alpha, beta=beta))
        for pid in positioner_id
    ]
    await asyncio.gather(*commands)

    return command.finish(text="current changed.")


@jaeger_parser.command()
async def stop(command, fps):
    """Stops the positioners and clear flags."""

    await fps.stop_trajectory()
    await fps.update_status(timeout=0.1)
    await fps.update_position()

    command.set_status(clu.CommandStatus.DONE, text="Trajectory aborted")


@jaeger_parser.command()
async def unlock(command, fps):
    """Unlocks the FPS."""

    result = await fps.unlock()

    if result:
        command.info(locked=False)
        return command.finish(text="FPS unlocked")
    else:
        return command.fail(error="failed to unlock FPS")


@jaeger_parser.command()
@click.argument("PATH", type=str)
async def trajectory(command, fps, path):
    """Sends a trajectory from a file."""

    if fps.moving:
        return command.fail(error="FPS is moving. Cannot send trajectory.")

    if fps.locked:
        return command.fail(
            error=f"FPS is locked by {fps.locked_by}. Cannot send trajectory."
        )

    path = pathlib.Path(path).expanduser()
    if not path.exists():
        raise click.BadParameter(f"path {path!s} does not exist.")

    try:

        trajectory = Trajectory(fps, path)

        command.debug(text="sending trajectory ...")
        await trajectory.send()
        if trajectory.failed:
            return command.fail(error="failed sending trajectory with unknown error.")

        command.debug(text=f"trajectory sent in {trajectory.data_send_time:.2f} s.")
        command.info(
            text=f"move will take {trajectory.move_time:.2f} s",
            move_time=f"{trajectory.move_time:.2f}",
        )

        await trajectory.start()
        if trajectory.failed:
            return command.fail(error="failed starting trajectory with unknown error.")

        return command.finish(text="trajectory completed.")

    except TrajectoryError as ee:
        return command.fail(error=str(ee))


@jaeger_parser.group()
def hall():
    """Turns the hall sensor on/off."""

    pass


@hall.command()
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
async def on(command, fps, positioner_id):
    """Turns the hall sensor on."""

    if positioner_id is None:
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=False):
        return

    command.debug("Turning hall sensors ON")
    await fps.send_command("HALL_ON", positioner_ids=positioner_id)

    command.debug("Waiting 5 seconds ...")
    await asyncio.sleep(5)

    command.finish()


@hall.command()
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
async def off(command, fps, positioner_id):
    """Turns the hall sensor off."""

    if positioner_id is None:
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=False):
        return

    command.debug("Turning hall sensors OFF")
    await fps.send_command("HALL_OFF", positioner_ids=positioner_id)

    command.finish()


@jaeger_parser.group()
def led():
    """Turns the positioner LED on/off."""

    pass


@led.command(name="on")
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
async def led_on(command, fps, positioner_id):
    """Turns the LED on."""

    if positioner_id is None:
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=False):
        return

    command.debug("Turning LED on")
    await fps.send_command("SWITCH_LED_ON", positioner_ids=positioner_id)

    command.finish()


@led.command(name="off")
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
async def led_off(command, fps, positioner_id):
    """Turns the LED off."""

    if positioner_id is None:
        positioner_id = list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps, initialised=False):
        return

    command.debug("Turning LED off")
    await fps.send_command("SWITCH_LED_OFF", positioner_ids=positioner_id)

    command.finish()


@jaeger_parser.command()
async def reload(command, fps):
    """Reinitialise the FPS."""

    try:
        await fps.initialise(start_pollers=fps.pollers.running)
    except BaseException as err:
        return command.fail(error=f"Initialisation failed: {err}")

    return command.finish(text="FPS was reinitialised.")
