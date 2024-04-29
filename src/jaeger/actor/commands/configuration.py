#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-18
# @Filename: configuration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from time import time

from typing import TYPE_CHECKING

import click
import numpy
import peewee
from astropy.time import Time

from clu.parsers.click import timeout
from sdssdb.peewee.sdss5db import opsdb

from jaeger import config
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.ieb import IEB
from jaeger.kaiju import check_trajectory
from jaeger.kaiju import explode as kaiju_explode
from jaeger.target.configuration import (
    Configuration,
    DitheredConfiguration,
    ManualConfiguration,
)
from jaeger.target.design import Design
from jaeger.target.tools import copy_summary_file, create_random_configuration
from jaeger.utils.database import get_designid_from_queue, match_assignment_hash

from . import jaeger_parser


if TYPE_CHECKING:
    from clu.command import Command

    from jaeger.actor import JaegerActor, JaegerCommandType
    from jaeger.fps import FPS


__all__ = ["configuration"]


async def _load_design(
    command: JaegerCommandType,
    fps: FPS,
    design_id: int | None = None,
    preload: bool = False,
    no_clone: bool = False,
    scale: float | None = None,
    fudge_factor: float | None = None,
    epoch: float | None = None,
    epoch_delay: float = 0.0,
    extra_epoch_delay: float = 0.0,
    boss_wavelength: float | None = None,
    apogee_wavelength: float | None = None,
    get_paths: bool = True,
    path_generation_mode: str | None = None,
    safety_factor: float = 1,
    offset_min_skybrightness: float = 0.5,
):
    """Helper to load or preload a design."""

    if design_id is None:
        design_id, _epoch_delay = get_designid_from_queue(
            pop=not preload,
            epoch_delay=True,
        )
        if epoch_delay == 0.0 and _epoch_delay is not None:
            epoch_delay = _epoch_delay + extra_epoch_delay
        else:
            epoch_delay += extra_epoch_delay

    if design_id is None:
        command.error("Failed getting a new design from the queue.")
        return False

    if preload:
        command.info(f"Preloading design {design_id}.")
    else:
        command.info(f"Loading design {design_id}.")

    valid = Design.check_design(design_id, command.actor.observatory)
    if valid is False:
        command.error(
            "The design does not exists or is not a valid "
            f"{command.actor.observatory} design."
        )
        return False

    # If the original configuration was create longer than max_cloned_time,
    # then do not clone again since we want a chance to update the positions
    # of the robots for the current epoch.
    max_cloned_time = config["configuration"]["max_cloned_time"]

    if (
        no_clone is False
        and fps.configuration is not None
        and time() - fps.configuration.created_time < max_cloned_time
        and fps.configuration.configuration_id is not None
        and fps.configuration.design is not None
        and match_assignment_hash(fps.configuration.design.design_id, design_id)
    ):
        command.info(
            f"Design {design_id} matches previously loaded design "
            f"{fps.configuration.design.design_id}. Cloning configuration."
        )

        configuration = await fps.configuration.clone(
            design_id=design_id,
            write_summary=False,
            write_to_database=False,
        )

    else:
        default_scale = config["configuration"].get("default_focal_scale", 1.0)
        use_guider_scale = config["configuration"].get("use_guider_scale", True)

        clip_scale: float = config["configuration"]["clip_scale"]
        SCALE_FUDGE: float = config["configuration"]["scale_fudge_factor"]
        fudge_factor = fudge_factor or SCALE_FUDGE

        guider_scale: float | None = None

        # Query the guider for the historical scale from the previous exposure.
        if scale is None and use_guider_scale is True:
            command.debug("Getting guider scale.")

            max_scale_age = config["configuration"]["guider_max_scale_age"]
            get_scale_cmd = await command.send_command(
                "cherno",
                f"get-scale --max-age {max_scale_age}",
            )
            if get_scale_cmd.status.did_fail:
                command.warning("Failed getting scale from guider.")
            else:
                guider_scale = float(get_scale_cmd.replies.get("scale_median")[0])
                if guider_scale < 0:
                    command.warning("Invalid guider scale.")
                    guider_scale = None
                else:
                    # Clip scale if needed.
                    if (abs(guider_scale) - 1) * 1e6 > clip_scale:
                        guider_scale = numpy.clip(
                            guider_scale,
                            1 - clip_scale / 1e6,
                            1 + clip_scale / 1e6,
                        )
                        command.warning(
                            "Unexpectedly large guider scale. "
                            f"Clipping to {guider_scale:.6f}."
                        )

            temp_coeffs = config["configuration"].get("scale_temperature_coeffs", None)

            if guider_scale is None and temp_coeffs is not None:
                # Try using the scale-temperature relationship instead.
                try:
                    if not isinstance(command.actor.fps.ieb, IEB):
                        raise ValueError("IEB not connected")

                    temperature = (await command.actor.fps.ieb.read_device("T3"))[0]
                    if not isinstance(temperature, float) or temperature < -100:
                        raise ValueError("Invalid ambient temperature")

                    guider_scale = float(numpy.polyval(temp_coeffs, temperature))
                    if command.actor.observatory == "LCO":
                        # HACK: we should redo this relation, but for now just use the
                        # measured change in guider scale after moving the IMB.
                        guider_scale *= 1.0018904537

                    command.debug(
                        "Using focal scale factor derived from ambient "
                        f"temperature ({temperature:.2f} C): {guider_scale:.6f}"
                    )
                except Exception as err:
                    command.warning(
                        f"Failed getting ambient temperature: {err} "
                        "Using default scale."
                    )

        # Second pass at setting the scale. Apply the fudge factor.
        if guider_scale is not None:
            scale = guider_scale * fudge_factor
            command.debug(
                f"Correcting guider scale {guider_scale:.6f} with fudge factor "
                f"{fudge_factor:.6f}."
            )

        if scale is not None:
            command.info(f"Focal scale: {scale:.6f}.")
        else:
            scale = default_scale
            command.info(f"Using default scale {scale:.6f}.")

        # Define the epoch for the configuration.
        try:
            command.debug(text=f"Epoch delay: {round(epoch_delay, 1)} seconds.")

            if epoch is None:
                epoch = float(Time.now().jd)
            epoch += epoch_delay / 86400.0

            design = await Design.create_async(
                design_id,
                epoch=epoch,
                scale=scale,
                safety_factor=safety_factor,
                offset_min_skybrightness=offset_min_skybrightness,
                boss_wavelength=boss_wavelength,
                apogee_wavelength=apogee_wavelength,
            )
        except Exception as err:
            command.error(error=f"Failed retrieving design: {err}")
            return False

        configuration = design.configuration

    if get_paths and configuration.is_cloned is False:
        command.info("Calculating trajectories.")
        configuration.set_command(command)
        await configuration.get_paths(
            decollide=True,
            path_generation_mode=path_generation_mode,
        )

    return configuration


@jaeger_parser.group()
def configuration():
    """Allows to load a configuration, ingest it to the database, and execute it."""
    pass


@configuration.command(cancellable=True)
@click.option(
    "--reload",
    is_flag=True,
    help="If the design is currently loaded, creates a new configuration.",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Replace an existing entry.",
)
@click.option(
    "--generate-paths/--no-generate-paths",
    default=True,
    help="Generates and stores the to and from destination paths.",
)
@click.option(
    "--epoch",
    type=float,
    help="Epoch at which to create the configuration, as a Julian Day.",
)
@click.option(
    "--epoch-delay",
    type=float,
    default=0.0,
    help="A delay in seconds for the epoch for which the configuration is calculated.",
)
@click.option(
    "--extra-epoch-delay",
    type=float,
    default=0.0,
    help="A delay in seconds to be added on top of the --epoch-delay. This is "
    "mainly used by HAL when it preloads a design ahead of time..",
)
@click.option(
    "--boss-wavelength",
    type=click.FloatRange(3000, 10000),
    help="Optimise positioning of BOSS fibres to this wavelength.",
)
@click.option(
    "--apogee-wavelength",
    type=click.FloatRange(16000, 17000),
    help="Optimise positioning of APOGEE fibres to this wavelength.",
)
@click.option(
    "--from-positions",
    is_flag=True,
    help="Loads a configuration from the current robot positions.",
)
@click.option(
    "--from-preloaded",
    is_flag=True,
    help="Finishes loading a preloaded configuration.",
)
@click.option(
    "--ingest/--no-ingest",
    default=True,
    help="Whether to ingest the configuration into the DB.",
)
@click.option(
    "--write-summary/--no-write-summary",
    default=True,
    help="Whether to write the summary file for the configuration. "
    "Ignored if --no-ingest.",
)
@click.option(
    "--execute/--no-execute",
    default=False,
    help="Send and start the from_destination trajectory.",
)
@click.option(
    "--reissue",
    is_flag=True,
    help="Only reissue the configuration_loaded keyword.",
)
@click.option(
    "--scale",
    type=float,
    help="Focal plane scale factor. If not passed, uses coordio default.",
)
@click.option(
    "--fudge-factor",
    type=float,
    default=None,
    help="Modify the default scale fudge factor.",
)
@click.option(
    "--no-clone",
    is_flag=True,
    help="If the new design has the same target set as the currently loaded one, "
    "does not clone the configuration and instead loads the new design.",
)
@click.option(
    "--path-generation-mode",
    type=click.Choice(["greedy", "mdp"], case_sensitive=False),
    help="The path generation algorithm to use.",
)
@click.option(
    "--safety-factor",
    type=float,
    default=1,
    help="Safety factor to pass to the offset calculation function.",
)
@click.option(
    "--offset-min-skybrightness",
    type=float,
    default=0.5,
    help="Minimum sky brightness for the offset.",
)
@click.argument("DESIGNID", type=int, required=False)
async def load(
    command: Command[JaegerActor],
    fps: FPS,
    designid: int | None = None,
    reload: bool = False,
    replace: bool = False,
    from_positions: bool = False,
    from_preloaded: bool = False,
    generate_paths: bool = False,
    epoch: float | None = None,
    epoch_delay: float = 0.0,
    extra_epoch_delay: float = 0.0,
    apogee_wavelength: float | None = None,
    boss_wavelength: float | None = None,
    ingest: bool = False,
    write_summary: bool = False,
    execute: bool = False,
    reissue: bool = False,
    scale: float | None = None,
    fudge_factor: float | None = None,
    no_clone: bool = False,
    path_generation_mode: str | None = None,
    safety_factor: float = 1,
    offset_min_skybrightness: float = 0.5,
):
    """Creates and ingests a configuration from a design in the database."""

    assert command.actor is not None

    if reissue is True:
        if fps.configuration is None or fps.configuration.design is None:
            return command.fail("No configuration loaded.")
        if designid is not None and designid != fps.configuration.design.design_id:
            return command.fail("Mismatch between loaded and provided design IDs.")
        _output_configuration_loaded(command, fps)
        return command.finish()

    if reload is True:
        if fps.configuration is None:
            return command.fail(error="No configuration found. Cannot reload.")
        designid = fps.configuration.design_id
        fps.configuration.configuration_id = None

    elif from_preloaded is True:
        if fps._preloaded_configuration is None:
            return command.fail("No preloaded configuration available.")

        fps.configuration = fps._preloaded_configuration

    elif from_positions is True:
        await fps.update_position()
        positions = fps.get_positions_dict()
        obs = command.actor.observatory
        fps.configuration = ManualConfiguration.create_from_positions(obs, positions)

    else:
        configuration = await _load_design(
            command,
            fps,
            design_id=designid,
            fudge_factor=fudge_factor,
            preload=False,
            no_clone=no_clone,
            scale=scale,
            epoch=epoch,
            epoch_delay=epoch_delay,
            extra_epoch_delay=extra_epoch_delay,
            boss_wavelength=boss_wavelength,
            apogee_wavelength=apogee_wavelength,
            get_paths=False,
            path_generation_mode=path_generation_mode,
            safety_factor=safety_factor,
            offset_min_skybrightness=offset_min_skybrightness,
        )

        if configuration is False:
            # _load_design already issues an error so we just fail.
            return command.fail()
        else:
            fps.configuration = configuration

    assert isinstance(fps.configuration, (Configuration, ManualConfiguration))

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.ingested is False:
        replace = False

    fps.configuration.set_command(command)

    if (
        not fps.configuration.is_cloned
        and generate_paths
        and fps.configuration.to_destination is None
        and not from_positions
    ):
        try:
            command.info("Calculating trajectories.")
            await fps.configuration.get_paths(
                decollide=not from_positions,
                path_generation_mode=path_generation_mode,
            )
        except Exception as err:
            return command.fail(error=f"Failed generating paths: {err}")

    if ingest:
        fps.configuration.write_to_database(replace=replace)
    else:
        command.warning("Not ingesting configuration. Configuration ID is -999.")
        fps.configuration.configuration_id = -999

    if ingest and write_summary:
        temperature = await fps.configuration.get_temperature()
        fps.configuration.write_summary(
            overwrite=True,
            headers={
                "cloned_from": fps.configuration.cloned_from or -999,
                "temperature": temperature,
            },
        )

        if fps.configuration.cloned_from and fps.configuration.configuration_id:
            copy_summary_file(
                fps.configuration.cloned_from,
                fps.configuration.configuration_id,
                fps.configuration.design_id,
                "F",
            )

    # Reset cherno offsets
    command.debug("Resetting cherno offsets.")
    await command.send_command("cherno", "offset")

    _output_configuration_loaded(command, fps)

    snapshot = await fps.configuration.save_snapshot()
    command.info(configuration_snapshot=snapshot)

    if execute and not from_positions:
        cmd = await command.send_command("jaeger", "configuration execute")
        if cmd.status.did_fail:
            if cmd.status.is_done:
                return
            else:
                return cmd.fail("Failed executing configuration.")

    fps._preloaded_configuration = None
    command.debug(design_preloaded=-999)

    if fps.configuration.design_id is not None and fps.configuration.design_id > 0:
        design_id = fps.configuration.design_id
        # Check if the design is in the queue and it would be the next one to pop.
        # If so, pop it.
        try:
            queue_instance = (
                opsdb.Queue.select()
                .where(opsdb.Queue.design_id == design_id)
                .order_by(opsdb.Queue.position)
                .first()
            )
            if queue_instance and queue_instance.position == 1:
                opsdb.Queue.pop()
        except peewee.DoesNotExist:
            pass

    return command.finish(f"Configuration {fps.configuration.configuration_id} loaded.")


@configuration.command()
async def reload(command: Command[JaegerActor], fps: FPS):
    """Reloads the current configuration.

    This is equivalent to using jaeger configuration load --no-clone DESIGNID
    where DESIGNID is the currently loaded design.

    """

    if (
        fps.configuration is None
        or fps.configuration.design_id is None
        or fps.configuration.design_id == -999
    ):
        return command.fail("There is no configuration currently loaded.")

    design_id = fps.configuration.design_id
    await command.child_command(f"configuration load --no-clone {design_id}")

    return command.finish()


def _output_configuration_loaded(command: Command[JaegerActor], fps: FPS):
    """Outputs the loaded configuration."""

    assert fps.configuration

    command.debug(text=f"Focal plane scale: {fps.configuration.assignment.scale}.")

    boresight = fps.configuration.assignment.boresight
    configuration = fps.configuration

    command.debug(
        configuration_loaded=[
            configuration.configuration_id,
            configuration.design.design_id if configuration.design else -999,
            configuration.design.field.field_id if configuration.design else -999,
            configuration.design.field.racen if configuration.design else -999.0,
            configuration.design.field.deccen if configuration.design else -999.0,
            configuration.design.field.position_angle if configuration.design else 0,
            boresight[0, 0] if boresight is not None else -999.0,
            boresight[0, 1] if boresight is not None else -999.0,
            configuration._summary_file or "",
            configuration.is_cloned,
        ]
    )


@configuration.command()
async def clone(command: Command[JaegerActor], fps: FPS):
    """Clones a configuration."""

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    new = await fps.configuration.clone()
    fps.configuration = new

    _output_configuration_loaded(command, fps)

    return command.finish()


@configuration.command(cancellable=True)
@click.option(
    "--epoch",
    type=float,
    help="Epoch at which to create the configuration, as a Julian Day.",
)
@click.option(
    "--epoch-delay",
    type=float,
    default=0.0,
    help="A delay in seconds for the epoch for which the configuration is calculated.",
)
@click.option(
    "--extra-epoch-delay",
    type=float,
    default=0.0,
    help="A delay in seconds to be added on top of the --epoch-delay. This is "
    "mainly used by HAL when it preloads a design ahead of time..",
)
@click.option(
    "--boss-wavelength",
    type=click.FloatRange(3000, 10000),
    help="Optimise positioning of BOSS fibres to this wavelength.",
)
@click.option(
    "--apogee-wavelength",
    type=click.FloatRange(16000, 17000),
    help="Optimise positioning of APOGEE fibres to this wavelength.",
)
@click.option(
    "--scale",
    type=float,
    help="Focal plane scale factor. If not passes, uses coordio default.",
)
@click.option(
    "--fudge-factor",
    type=float,
    default=None,
    help="Modify the default scale fudge factor.",
)
@click.option(
    "--no-clone",
    is_flag=True,
    help="If the new design has the same target set as the currently loaded one, "
    "does not clone the configuration and instead loads the new design.",
)
@click.option(
    "--make-active",
    is_flag=True,
    help="Loads the configuration after preloading.",
)
@click.option(
    "--clear",
    is_flag=True,
    help="Clears the preloaded configuration.",
)
@click.option(
    "--safety-factor",
    type=float,
    default=1,
    help="Safety factor to pass to the offset calculation function.",
)
@click.option(
    "--offset-min-skybrightness",
    type=float,
    default=0.5,
    help="Minimum sky brightness for the offset.",
)
@click.argument("DESIGNID", type=int, required=False)
@timeout(300)
async def preload(
    command: JaegerCommandType,
    fps: FPS,
    designid: int | None = None,
    epoch: float | None = None,
    epoch_delay: float = 0.0,
    extra_epoch_delay: float = 0.0,
    apogee_wavelength: float | None = None,
    boss_wavelength: float | None = None,
    scale: float | None = None,
    fudge_factor: float | None = None,
    no_clone: bool = False,
    make_active: bool = True,
    clear: bool = False,
    safety_factor: float = 1,
    offset_min_skybrightness: float = 0.5,
):
    """Preloads a design.

    Preloading a design works similar to loading it, but no files are generated, no
    database entry is created, no configuration_loaded keyword is output, and the
    new configuration is stored separately. To make the preloaded configuration
    active either pass the --make-active flag or run
    jaeger configuration load --from-preloaded.

    """

    if clear:
        fps._preloaded_configuration = None
        return command.finish(design_preloaded=-999)

    configuration = await _load_design(
        command,
        fps,
        design_id=designid,
        preload=True,
        no_clone=no_clone,
        scale=scale,
        fudge_factor=fudge_factor,
        epoch=epoch,
        epoch_delay=epoch_delay,
        extra_epoch_delay=extra_epoch_delay,
        boss_wavelength=boss_wavelength,
        apogee_wavelength=apogee_wavelength,
        safety_factor=safety_factor,
        offset_min_skybrightness=offset_min_skybrightness,
    )

    if configuration is False:
        # _load_design already issues an error so we just fail.
        return command.fail()
    elif configuration.design_id is None:
        return command.fail(
            "Preloaded configuration does not have design ID. "
            "This should never happen."
        )
    else:
        fps._preloaded_configuration = configuration

    command.info(
        design_preloaded=configuration.design_id,
        preloaded_is_cloned=configuration.is_cloned,
    )

    if make_active:
        load_cmd = await command.send_command(
            "jaeger",
            "configuration load --from-preloaded",
        )
        if load_cmd.status.did_fail:
            return command.fail("Failed making the preloaded configuration active.")

    return command.finish()


@configuration.command()
async def execute(command: Command[JaegerActor], fps: FPS):
    """Executes a configuration trajectory (folded to targets)."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    if fps.configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if fps.configuration.is_dither:
        assert isinstance(fps.configuration, DitheredConfiguration)
        if fps.configuration.to_destination is not None:
            trajectory = fps.configuration.to_destination
            command.info(text="Using stored trajectory (to destination).")
        else:
            command.info(text="Calculating trajectory.")
            trajectory = await fps.configuration.get_paths()
    else:
        if fps.configuration.from_destination is not None:
            trajectory = fps.configuration.from_destination
            command.info(text="Using stored trajectory (from destination).")
        else:
            command.info(text="Calculating trajectory.")

            try:
                trajectory = await fps.configuration.get_paths()
            except Exception as err:
                return command.fail(error=f"Failed getting trajectory: {err}")

        if not (await check_trajectory(trajectory, fps=fps, atol=1)):
            return command.fail(error="Trajectory validation failed.")

    command.info(text="Sending and executing forward trajectory.")

    try:
        await fps.send_trajectory(trajectory, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    fps.configuration.executed = True

    command.finish()


@configuration.command()
@click.option("--explode", is_flag=True, help="Explode before reversing.")
async def reverse(command: Command[JaegerActor], fps: FPS, explode: bool = False):
    """Executes a reverse trajectory (targets to folded)."""

    if fps.locked:
        return command.fail(error="The FPS is locked.")

    if await fps.is_folded():
        return command.finish("Already folded.")

    configuration = fps.configuration
    if (fps.configuration is None or fps.configuration.executed is False) and (
        len(fps._previous_configurations) > 0
        and fps._previous_configurations[-1] is not None
        and fps._previous_configurations[-1].executed is True
    ):
        configuration = fps._previous_configurations[-1]
        command.warning("Using previous configuration reverse path.")

    if configuration is None:
        return command.fail(error="A configuration must first be loaded.")

    if configuration.executed is False:
        return command.fail(error="The configuration was not executed. Try unwinding.")

    await fps.update_position()

    if configuration.is_dither:
        trajectory = configuration.from_destination
    else:
        trajectory = configuration.to_destination
        if trajectory is None:
            return command.fail(
                error="The configuration does not have a to_destination "
                "path. Use unwind."
            )

        try:
            if explode:
                # First we explode the robots a bit.
                command.info("Exploding before reversing.")
                current_positions = fps.get_positions_dict()
                explode_path = await kaiju_explode(
                    current_positions,
                    5.0,
                    disabled=[pid for pid in fps.positioners if fps[pid].disabled],
                )
                await fps.send_trajectory(explode_path, command=command)

                # Then we send a goto to the initial point of the reverse trajectory.
                new_positions = {
                    pid: (trajectory[pid]["alpha"][0][0], trajectory[pid]["beta"][0][0])
                    for pid in trajectory
                }
                command.info("Reverting to final configuration positions.")
                await fps.goto(new_positions)

        except Exception as err:
            return command.fail(f"Failed preparing to send reverse trajectory: {err}")

    command.info(text="Sending and executing reverse trajectory.")

    try:
        await fps.send_trajectory(trajectory, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    if configuration.is_dither:
        command.info("Restoring parent configuration.")
        fps.configuration = configuration.parent_configuration
        _output_configuration_loaded(command, fps)

    command.finish()


@configuration.command()
@click.argument("RADIUS", type=float)
@click.option(
    "--epoch",
    type=str,
    default="inherit",
    help="Epoch to use for the dithered configuration. With 'inherit', the parent "
    "configuration epoch is used; 'now' uses the current time. Otherwise provide a JD.",
)
async def dither(
    command: Command[JaegerActor],
    fps: FPS,
    radius: float,
    epoch: str = "inherit",
):
    """Dither a loaded configuration."""

    if fps.configuration is None:
        return command.fail("A configuration must first be loaded.")

    is_dithered = isinstance(fps.configuration, DitheredConfiguration)
    if is_dithered and fps.configuration.parent_configuration is not None:
        parent_configuration = fps.configuration.parent_configuration
    else:
        parent_configuration = fps.configuration

    configuration_id = parent_configuration.configuration_id
    command.info(f"Creating dithered configuration from {configuration_id}")

    epoch = epoch.lower()
    if epoch == "inherit":
        dither_epoch = None
    elif epoch == "now":
        dither_epoch = Time.now().jd
    else:
        dither_epoch = float(epoch)

    fps.configuration = DitheredConfiguration(
        parent_configuration,
        radius,
        epoch=dither_epoch,
    )
    await fps.configuration.get_paths()

    fps.configuration.write_to_database()
    fps.configuration.write_summary(overwrite=True)

    _output_configuration_loaded(command, fps)

    command.info("Executing dithered configuration.")
    execute_cmd = await command.send_command("jaeger", "configuration execute")
    if execute_cmd.status.did_fail:
        command.fail("Failed executing configuration.")

    fps.configuration.executed = True

    configuration_id = fps.configuration.configuration_id
    command.finish(f"Dithered configuration {configuration_id} loaded and executed.")


@configuration.command()
@click.argument("POSITION_ANGLE", type=float, required=False)
async def slew(
    command: Command[JaegerActor],
    fps: FPS,
    position_angle: float | None = None,
):
    """Slews to the field centre of a configuration.

    Optionally pass the position angle of the rotator in DEGREES.

    """

    command.warning(
        "jaeger configuration slew is a temporary command that does not "
        "perform any safety checks. It will be replaced with hal goto."
    )

    if fps.configuration is None:
        return command.fail("Configuration not loaded.")

    if isinstance(fps.configuration, DitheredConfiguration):
        return command.fail("Cannot slew to a dithered configuration.")

    if fps.configuration.design is None:
        return command.fail("The configuration does not have a design.")

    ra = fps.configuration.design.field.racen
    dec = fps.configuration.design.field.deccen

    if position_angle is not None:
        pa = position_angle
    else:
        pa = fps.configuration.design.field.position_angle

    command.info(f"Slewing to ({ra}, {dec}, {pa})")

    if fps.observatory == "APO":
        slew_cmd = await command.send_command(
            "tcc",
            f"track {ra}, {dec} icrs /rottype=object /rotang={pa:g} /rotwrap=mid",
        )
    else:
        slew_cmd = await command.send_command(
            "lcotcc",
            f"target {ra}, {dec} /posAngle={pa:.3f}",
        )

    if slew_cmd.status.did_fail:
        return command.fail("Failed slewing to field.")

    return command.finish("Slew complete.")


@configuration.command()
@click.argument("SEED", type=int, required=False)
@click.option("--danger", is_flag=True, help="Use full range of alpha and beta.")
@click.option(
    "--uniform",
    type=str,
    default=None,
    help="Comma-separated alpha and beta ranges.",
)
@click.option(
    "--collision-buffer",
    type=click.FloatRange(1.6, 3.0),
    help="Custom collision buffer",
)
@click.option(
    "--send-trajectory/--no-send-trajectory",
    help="Send the trajectory to the FPS.",
    default=True,
)
@click.option(
    "--max-retries",
    type=int,
    default=10,
    help="Home many retries to allow due to deadlocks.",
)
@click.option(
    "--path-generation-mode",
    type=click.Choice(["greedy", "mdp"], case_sensitive=False),
    help="The path generation algorithm to use.",
)
async def random(
    command: Command[JaegerActor],
    fps: FPS,
    seed: int | None = None,
    danger: bool = False,
    uniform: str | None = None,
    collision_buffer: float | None = None,
    send_trajectory: bool = True,
    max_retries: int = 10,
    path_generation_mode: str | None = None,
):
    """Executes a random, valid configuration."""

    if fps.locked:
        command.fail(error="The FPS is locked.")

    command.debug(text="Checking that all positioners are folded.")

    # Check that all positioners are folded.
    await fps.update_position()
    positions = fps.get_positions(ignore_disabled=True)

    if len(positions) == 0:
        return command.fail("No positioners connected")

    alphaL: float
    betaL: float

    alphaL, betaL = config["kaiju"]["lattice_position"]
    if not numpy.allclose(positions[:, 1:] - [alphaL, betaL], 0, atol=1):
        return command.fail(error="Not all the positioners are folded.")

    command.info(text="Creating random configuration.")

    uniform_unpack: tuple[float, ...] | None
    if uniform:
        try:
            uniform_unpack = tuple(map(float, uniform.split(",")))
        except Exception as err:
            return command.fail(error=f"Failed calculating uniform ranges: {err}")
    else:
        uniform_unpack = None

    try:
        configuration = await create_random_configuration(
            fps,
            seed=seed,
            uniform=uniform_unpack,
            safe=not danger,
            collision_buffer=collision_buffer,
            max_retries=max_retries,
        )

        command.info("Getting trajectory.")
        trajectory = await configuration.get_paths(
            decollide=False,
            collision_buffer=collision_buffer,
            path_generation_mode=path_generation_mode,
        )
    except JaegerError as err:
        return command.fail(error=f"jaeger random failed: {err}")

    # Make this the FPS configuration
    assert command.actor
    command.actor.fps.configuration = configuration

    if send_trajectory is False:
        return command.finish()

    command.info("Executing random trajectory.")

    try:
        await fps.send_trajectory(trajectory, command=command)
    except TrajectoryError as err:
        return command.fail(error=f"Trajectory failed with error: {err}")

    configuration.executed = True

    command.finish()


@configuration.command()
@click.argument("RA", type=float)
@click.argument("DEC", type=float)
@click.argument("PA", type=float)
async def fake_field(
    command: Command[JaegerActor],
    fps: FPS,
    ra: float,
    dec: float,
    pa: float,
):
    """Output a fake field."""

    command.info(
        configuration_loaded=[
            -999,
            -999,
            -999,
            ra,
            dec,
            pa,
            -999.0,
            -999.0,
            "?",
            False,
        ]
    )

    return command.finish()
