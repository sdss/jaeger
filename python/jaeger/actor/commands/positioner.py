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
import numpy

import clu

from jaeger.commands import SetCurrent, Trajectory
from jaeger.exceptions import TrajectoryError
from jaeger.utils import get_goto_move_time

from . import jaeger_parser


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
@click.argument("POSITIONER-ID", type=int, nargs=-1)
@click.argument("ALPHA", type=click.FloatRange(-360.0, 360.0))
@click.argument("BETA", type=click.FloatRange(-360.0, 360.0))
@click.option(
    "-r",
    "--relative",
    is_flag=True,
    help="whether this is a relative move",
)
@click.option(
    "-s",
    "--speed",
    type=click.FloatRange(0.0, 2000.0),
    nargs=2,
    help="the speed of both alpha and beta arms, in RPS on the input.",
)
@click.option(
    "-a",
    "--all",
    is_flag=True,
    default=False,
    help="applies to all valid positioners.",
)
@click.option(
    "-f",
    "--force",
    is_flag=True,
    default=False,
    help="forces a move to happen.",
)
async def goto(command, fps, positioner_id, alpha, beta, speed, all, force, relative):
    """Sends positioners to a given (alpha, beta) position."""

    if all:
        if not force:
            return command.fail(
                error="need to specify --force to move all positioners at once."
            )
        positioner_id = list(fps.positioners.keys())

    if not relative:
        if alpha < 0 or beta < 0:
            return command.fail(error="negative angles only allowed in relative mode.")

    if not check_positioners(positioner_id, command, fps, initialised=True):
        return

    if fps.moving:
        return command.fail(error="FPS is moving. Cannot send goto.")

    speed = speed or [None, None]
    max_time = 0.0

    tasks = []
    for pid in positioner_id:

        # Manually calculate the max move time we'll encounter.
        p_alpha, p_beta = fps[pid].position

        if p_alpha is None or p_beta is None:
            return command.fail(error="some positioners do not know their positions.")

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

        tasks.append(
            fps.positioners[pid].goto(
                alpha,
                beta,
                speed=speed,
                relative=relative,
            )
        )

    command.info(move_time=round(max_time, 2))

    result = await clu.as_complete_failer(tasks, on_fail_callback=fps.stop_trajectory)

    if not result[0]:
        error_message = result[1] or "goto command failed"
        command.set_status(clu.CommandStatus.FAILED, error=error_message)
    else:
        command.set_status(clu.CommandStatus.DONE, text="Position reached")


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
@click.argument("POSITIONER-ID", type=int, nargs=-1, required=False)
@click.option(
    "-f",
    "--full",
    is_flag=True,
    default=False,
    help="outputs more statuses.",
)
async def status(command, fps, positioner_id, full):
    """Reports the position and status bit of a list of positioners."""

    positioner_ids = positioner_id or list(fps.positioners.keys())

    if not check_positioners(positioner_id, command, fps):
        return

    command.info(locked=fps.locked)

    if fps.engineering_mode:
        command.warning(engineering_mode=True)
    else:
        command.info(engineering_mode=False)

    command.info(n_positioners=len(fps.positioners))

    for pid in sorted(positioner_ids):
        p = fps[pid]

        alpha_pos = -999 if p.alpha is None else numpy.round(p.alpha, 4)
        beta_pos = -999 if p.beta is None else numpy.round(p.beta, 4)

        if pid in fps.positioner_to_bus:
            interface, bus = fps.positioner_to_bus[pid]
            interface = fps.can.interfaces.index(interface) + 1
        else:
            interface = "NA"
            bus = -1

        command.write(
            "i",
            status=[
                p.positioner_id,
                alpha_pos,
                beta_pos,
                int(p.status),
                p.initialised,
                p.is_bootloader() or False,
                p.firmware or "?",
                interface,
                bus,
            ],
        )

    command.info(low_temperature=command.actor.low_temperature.value)

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
        fps.send_command(SetCurrent(positioner_id=pid, alpha=alpha, beta=beta))
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

    if not fps.locked:
        command.info(locker=False)
        return command.finish(text="FPS is not locked")

    result = await fps.unlock()

    if result:
        command.info(locker=False)
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
        return command.fail(error="FPS is locked. Cannot send trajectory.")

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
    await fps.send_to_all("HALL_ON", positioners=positioner_id)

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
    await fps.send_to_all("HALL_OFF", positioners=positioner_id)

    command.finish()
