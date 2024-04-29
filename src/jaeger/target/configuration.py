#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-10
# @Filename: configuration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import logging
import os
import pathlib
import shutil
from copy import deepcopy
from time import time

from typing import TYPE_CHECKING, Generic, Literal, Optional, TypeVar, Union

import numpy
import polars
import polars.selectors as cs
from astropy.table import Table
from astropy.time import Time

from coordio import (
    ICRS,
    Observed,
    PositionerApogee,
    PositionerBoss,
)
from coordio import __version__ as coordio_version
from coordio.defaults import (
    INST_TO_WAVE,
    calibration,
)
from kaiju import __version__ as kaiju_version
from sdssdb.peewee.sdss5db import opsdb, targetdb
from sdsstools._vendor.yanny import write_ndarray_to_yanny
from sdsstools.time import get_sjd

from jaeger import FPS, config, log
from jaeger import __version__ as jaeger_version
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.ieb import IEB
from jaeger.kaiju import (
    decollide_in_executor,
    dump_robot_grid,
    get_path_pair_in_executor,
    get_robot_grid,
    get_snapshot_async,
    load_robot_grid,
    warn,
)
from jaeger.target.assignment import Assignment, BaseAssignment, ManualAssignment
from jaeger.target.tools import copy_summary_file, get_fibermap_table, get_wok_data
from jaeger.utils.database import connect_database
from jaeger.utils.helpers import run_in_executor


if TYPE_CHECKING:
    from clu import Command

    from jaeger.actor import JaegerActor
    from jaeger.kaiju import TrajectoryType
    from jaeger.target.assignment import NewPositionsType
    from jaeger.target.design import Design


__all__ = [
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "DitheredConfiguration",
]

PositionerType = Union[PositionerApogee, PositionerBoss]
AssignmentType = TypeVar(
    "AssignmentType",
    bound=BaseAssignment,
    covariant=True,
)


class BaseConfiguration(Generic[AssignmentType]):
    """A base configuration class."""

    assignment: AssignmentType
    epoch: float | None

    def __init__(self, fps: FPS | None = None, scale: float | None = None):
        if len(calibration.positionerTable) == 0:
            raise ValueError("FPS calibrations not loaded or the array is empty.")

        # Configuration ID is None until we insert in the database.
        # Once set, it cannot be changed.
        self.configuration_id: int | None = None
        self._summary_file: str | None = None

        self.scale = scale or 1.0

        # Whether the configuration is a dither. If True, there will be a base
        # configuration from which we dithered and the trajectory will be applied
        # ignoring collisions and with early exit for deadlocks.
        self.parent_configuration: BaseConfiguration | None = None
        self.is_dither: bool = False

        self.is_cloned: bool = False
        self.cloned_from: int | None = None

        self.design: Design | None = None
        self.design_id: int | None = None

        self.extra_summary_data = {}

        self.fps = fps
        self.robot_grid = self._initialise_grid()

        self.command: Command[JaegerActor] | None = None

        self._decollided: list[int] = []

        self.to_destination: TrajectoryType = None
        self.from_destination: TrajectoryType = None

        self.created_time = time()
        self.executed: bool = False

    def __deepcopy__(self, memo):
        cls = self.__class__
        result = cls.__new__(cls)
        memo[id(self)] = result
        for k, v in self.__dict__.items():
            if k in ["robot_grid", "command"]:
                v = None
            setattr(result, k, deepcopy(v, memo))
        return result

    @property
    def fibre_data(self):
        """Returns the fibre data from the assignment data object."""

        return self.assignment.fibre_data

    @fibre_data.setter
    def fibre_data(self, value: polars.DataFrame):
        """Sets the fibre data in the assignment data object."""

        self.assignment.fibre_data = value

    async def clone(
        self,
        design_id: int | None = None,
        copy_summary_F: bool = False,
        write_to_database: bool = True,
        write_summary: bool = True,
    ) -> BaseConfiguration:
        """Clones a configuration.

        Parameters
        ----------
        design_id
            If set, modifies the ``design_id`` in the configuration `.Design`
            instance and copied summary files.
        copy_summary_F
            If `True` and a `confSummaryF` exists for the current configuration,
            copies it with the newly assigned configuration_id.
        write_to_database
            Write new configuration to database
        write_summary
            Write summary file for the new configuration.

        Returns
        -------
        configuration
            The cloned configuration.

        """

        assert self.configuration_id is not None, "configuration_id not set."

        original_configuration_id = self.configuration_id

        new = self.copy()
        new.robot_grid = self.robot_grid

        new.configuration_id = None
        new.is_cloned = True
        new.cloned_from = original_configuration_id

        if design_id is not None and new.design_id is not None:
            if new.design:
                new.design.design_id = design_id
            new.design_id = design_id

        if write_to_database:
            new.write_to_database()

        if write_to_database and write_summary:
            temperature = await self.get_temperature()
            new.write_summary(
                headers={
                    "cloned_from": new.cloned_from,
                    "temperature": temperature,
                }
            )

        if write_to_database and copy_summary_F:
            assert new.configuration_id is not None
            copy_summary_file(
                original_configuration_id,
                new.configuration_id,
                new.design_id,
                "F",
            )

        return new

    def copy(self):
        """Returns a deep copy of the configuration instance. Drops the robot grid."""

        return deepcopy(self)

    def set_command(self, command: Optional[Command[JaegerActor]]):
        """Sets the command to which to output messages."""

        self.command = command

    def log(self, msg: str, level=logging.INFO, to_command: bool = True):
        """Log message to the log and command."""

        log.log(level, msg)

        if to_command and self.command and self.command.status.is_done is False:
            if level == logging.DEBUG:
                self.command.debug(msg)
            elif level == logging.INFO:
                self.command.info(msg)
            elif level == logging.WARNING:
                self.command.warning(msg)
            elif level == logging.ERROR:
                self.command.error(msg)

    def _initialise_grid(self, collision_buffer: float | None = None):
        """Create a robot grid."""

        fps = self.fps or FPS.get_instance()

        self.robot_grid = get_robot_grid(fps, collision_buffer=collision_buffer)

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

    async def get_temperature(self):
        """Returns the T3 temperature."""

        fps = self.fps or FPS.get_instance()

        if fps and isinstance(fps.ieb, IEB):
            temp: float = (await fps.ieb.read_device("T3"))[0]
        else:
            temp = -999.0

        return round(temp, 1)

    def recompute_coordinates(self, epoch: Optional[float] = None):
        """Recalculates the coordinates.

        Parameters
        ----------
        epoch
            The Julian Date for which to compute the coordinates.

        """

        if self.configuration_id is not None:
            raise JaegerError(
                "Cannot recompute coordinates once the configuration "
                "ID has been set."
            )

        self.assignment.compute_coordinates(epoch=epoch)

        assert self.assignment.site.time
        self.epoch = self.assignment.site.time.jd

    async def get_paths(
        self,
        collision_buffer: float | None = None,
        decollide: bool = True,
        simple_decollision: bool = False,
        resolve_deadlocks: bool = True,
        n_deadlock_retries: int = 5,
        path_generation_mode: str | None = None,
        force: bool = False,
    ) -> dict:
        """Returns a trajectory dictionary from the folded position.

        Also stores the to destination trajectory so that it can be
        used later to return to folded position.

        Parameters
        ----------
        collision_buffer
            The collision buffer to use when generating paths. If `None`, defaults
            to the configuration file value.
        decollide
            Runs the decollision routine.
        simple_decollision
            If `True`, runs `decollideGrid()` without trying to prioritise and
            minimise what robots move.
        resolve_deadlocks
            Whether to solve for deadlocks after decollision.
        n_deadlock_retries
            How many times to try solving deadlocks.
        path_generation_mode
            The path generation mode, either ``'greedy'`` or ``'mdp'``. If not
            defined, uses the default path generation mode.
        force
            If `False`, fails if the robot grid is deadlocked. Always fails if there
            are collisions.

        Returns
        -------
        from_destination
            Returns the ``from destination`` trajectory (usually from lattice to
            targets).

        """

        assert isinstance(self, BaseConfiguration)

        # Just to be sure, reinitialise the grid.
        self.robot_grid = self._initialise_grid(collision_buffer=collision_buffer)

        alpha0, beta0 = config["kaiju"]["lattice_position"]

        # Assign positions to all the assigned, valid targets.
        # TODO: remove disabled from here.
        valid = self.fibre_data.filter(polars.col.assigned & polars.col.valid)

        self.log(
            f"Assigned targets {self.fibre_data['assigned'].sum()}. "
            f"Valid targets {len(valid)}."
        )

        invalid = []
        for robot in self.robot_grid.robotDict.values():
            if robot.isOffline:
                self.fibre_data = self.fibre_data.with_columns(
                    polars.when(polars.col.positioner_id == robot.id)
                    .then(True)
                    .otherwise(polars.col.offline)
                    .alias("offline")
                )
                invalid.append(robot.id)
                continue

            if robot.id not in valid["positioner_id"]:
                robot.setAlphaBeta(alpha0, beta0)
                robot.setDestinationAlphaBeta(alpha0, beta0)
                robot.setXYUniform()  # Scramble unassigned robots.
                invalid.append(robot.id)
            else:
                vrow = valid.row(
                    by_predicate=(polars.col.positioner_id == robot.id),
                    named=True,
                )
                robot.setAlphaBeta(vrow["alpha"], vrow["beta"])
                robot.setDestinationAlphaBeta(alpha0, beta0)

        self.update_coordinates_from_robot_grid(positioner_ids=invalid)

        # The invalid coordinates should now be valid but off target. We mark them as
        # reassigned to keep track of targets that have been moved not because they
        # were collided or for other reason.
        invalid_idx = self.fibre_data["positioner_id"].is_in(invalid).arg_true()
        self.fibre_data[invalid_idx, "reassigned"] = True

        decollided: list[int] = []
        if decollide:
            priority_order = valid["positioner_id"].to_list()

            self.robot_grid, decollided = await decollide_in_executor(
                self.robot_grid,
                simple=simple_decollision,
                priority_order=priority_order,
            )

            if len(decollided) > 0:
                self.log(
                    f"{len(decollided)} positioners were collided and "
                    f"have been reassigned: {decollided}.",
                    level=logging.WARNING,
                    to_command=False,
                )

                self.update_coordinates_from_robot_grid(positioner_ids=decollided)

            # Final check for collisions.
            if len(self.robot_grid.getCollidedRobotList()) > 0:
                raise TrajectoryError("The robot grid remains collided.")

        # Fix deadlocks (this sets the trajectories in the instance).
        self.log("Generating path pair.")
        n_retries = n_deadlock_retries if resolve_deadlocks else -1
        unlocked = await self._resolve_deadlocks(
            n_retries=n_retries,
            path_generation_mode=path_generation_mode,
            force=force,
        )

        if len(unlocked) > 0:
            self.update_coordinates_from_robot_grid(positioner_ids=unlocked)

        # Mark decollided (and unlocked) positioner_ids. First we get the
        # indices of those rows.
        self._decollided = list(set(decollided + unlocked))
        idx = self.fibre_data["positioner_id"].is_in(self._decollided).arg_true()

        # Now modify the frame in place.
        self.fibre_data[idx, "decollided"] = True
        self.fibre_data[idx, "reassigned"] = True  # All decollided have been reassigned

        if self.from_destination is None:
            raise TrajectoryError("Cannot find valid trajectory.")

        return self.from_destination

    async def _resolve_deadlocks(
        self,
        n_retries: int = 5,
        path_generation_mode: str | None = None,
        force: bool = False,
    ) -> list[int]:
        """Iteratively fix deadlocks."""

        # Save the grid data in case we need to decollide.
        grid_data = dump_robot_grid(self.robot_grid)

        attempt: int = 0
        decollided: list[int] = []

        while True:
            result = await get_path_pair_in_executor(
                self.robot_grid,
                path_generation_mode=path_generation_mode,
            )
            self.to_destination, self.from_destination, did_fail, deadlocks = result

            n_deadlocks = len(deadlocks)

            if did_fail:
                attempt += 1

                if n_retries < 0:
                    # n_retries == -1 means we don't want to solve for deadlocks. Fail!
                    raise TrajectoryError(
                        "Failed generating a valid trajectory. "
                        f"{n_deadlocks} deadlocks were found."
                    )

                if attempt > n_retries:
                    msg = (
                        f"Attempt {attempt}: {n_deadlocks} deadlocks remain but "
                        "the number of retries has been exhausted."
                    )

                    if force is False:
                        raise TrajectoryError(msg)

                    else:
                        self.log(msg, level=logging.WARNING, to_command=False)

                # Replace one of the deadlocked robots with a random new position.
                # TODO: maybe not call setXYUniform and do a small offset.

                if attempt == 1:
                    self.log("Deadlocks found. Attempting resolution.")

                self.log(f"Attempt {attempt}: {n_deadlocks} deadlocks found.")

                to_move = numpy.random.choice(deadlocks)
                self.log(f"Trying to unlock positioner {to_move}.", level=logging.DEBUG)

                # Restore robot grid (it has been mangled by calling get_path_pair).
                self.robot_grid = load_robot_grid(grid_data)

                # Assign new position to the random deadlocked robot.
                self.robot_grid.robotDict[to_move].setXYUniform()

                # Now check if it's collided and decollide it.
                if self.robot_grid.isCollided(to_move):
                    if self.robot_grid.robotDict[to_move].isOffline is False:
                        self.robot_grid.decollideRobot(to_move)
                        if self.robot_grid.isCollided(to_move):
                            raise TrajectoryError("Cannot decollide deadlocked robot.")

                if to_move not in decollided:
                    decollided.append(to_move)

                grid_data = dump_robot_grid(self.robot_grid)

            else:
                if attempt > 1:
                    self.log("All deadlocks have been fixed.")
                break

        return decollided

    def update_coordinates_from_robot_grid(
        self,
        positioner_ids: list[int] | None = None,
        mark_off_target: bool = True,
    ):
        """Updates the coordinates of robots from a robot grid.

        This method is expected to be called after `.get_paths` has been called
        and the robot grid has been updated. It updates the ``[xyz]wok_kaiju``
        columns for all the positioners. Additionally, updates the alpha and
        beta values and upstream coordinates for the positioners in
        ``positioner_ids`` (or all the positioners is ``positioner_ids=None``).

        If ``mark_off_target=True``, sets the ``on_target`` column to ``False``
        for the list of ``positioner_ids``.

        """

        if positioner_ids is None:
            positioner_ids = self.fibre_data["positioner_ids"].unique().to_list()

        n_positioners = len(positioner_ids)
        if n_positioners > 0:
            self.log(f"Recomputing {n_positioners} coordinates.", level=logging.DEBUG)

        new_alpha_beta: NewPositionsType = {}

        # Update the [xyz]wok_kaiju columns with the values the kaiju uses.
        # These should (!) be identical to [xyz]wok. We do this for all the
        # positioners in the grid.
        for robot in self.robot_grid.robotDict.values():
            for fibre_type in ["APOGEE", "BOSS", "Metrology"]:
                if fibre_type == "APOGEE":
                    kaiju_wok = robot.apWokXYZ
                elif fibre_type == "BOSS":
                    kaiju_wok = robot.bossWokXYZ
                elif fibre_type == "Metrology":
                    kaiju_wok = robot.metWokXYZ
                else:
                    raise ValueError(f"Invalid fibre type {fibre_type}.")

                idx = (
                    (self.fibre_data["positioner_id"] == robot.id)
                    & (self.fibre_data["fibre_type"] == fibre_type)
                ).arg_true()

                for icol, col in enumerate(["xwok_kaiju", "ywok_kaiju", "zwok_kaiju"]):
                    self.fibre_data[idx, col] = kaiju_wok[icol]

            # If the robot is in the list it means now it's at a different position
            # now, so we need to update its coordinates in the table. For now we just
            # create a dictionary with the new positions.
            if robot.id in positioner_ids:
                new_alpha_beta[robot.id] = {"alpha": robot.alpha, "beta": robot.beta}

        self.assignment.update_positioner_coordinates(new_alpha_beta)

        if mark_off_target:
            idx = self.fibre_data["positioner_id"].is_in(positioner_ids).arg_true()
            self.fibre_data[idx, "on_target"] = False

    async def save_snapshot(self, highlight=None):
        """Saves a snapshot of the current robot grid."""

        if self.assignment and self.assignment.observatory:
            observatory = self.assignment.observatory.upper()
        else:
            observatory = None

        mjd = get_sjd(observatory)
        dirpath = os.path.join(config["fps"]["configuration_snapshot_path"], str(mjd))
        if not os.path.exists(dirpath):
            os.makedirs(dirpath)

        cid = self.configuration_id or -999

        path = os.path.join(dirpath, f"configuration_snapshot_{mjd}_{cid}.pdf")

        data = dump_robot_grid(self.robot_grid)

        title = None
        if self.configuration_id:
            title = f"Configuration {self.configuration_id}"

        await run_in_executor(
            get_snapshot_async,
            path,
            data=data,
            highlight=highlight or self._decollided,
            title=title,
            executor="process",
        )

        return path

    @property
    def ingested(self):
        """Returns `True` if the configuration has been loaded to opsdb."""

        if self.configuration_id is None:
            return False

        return (
            opsdb.Configuration.select()
            .where(opsdb.Configuration.configuration_id == self.configuration_id)
            .exists()
        )

    def write_to_database(self, replace=False):
        """Writes the configuration to the database."""

        if connect_database(targetdb.database) is False:
            raise RuntimeError("Cannot connect to database.")

        if "admin" in targetdb.database._config:
            targetdb.database.become_admin()

        assert self.assignment.site.time
        epoch = self.assignment.site.time.jd

        if self.configuration_id is None:
            with opsdb.database.atomic():
                if self.design is None:
                    if (
                        targetdb.Design()
                        .select()
                        .where(targetdb.Design.design_id == self.design_id)
                        .exists()
                    ):
                        design_id = self.design_id
                    else:
                        design_id = None
                else:
                    design_id = self.design.design_id

                configuration = opsdb.Configuration(
                    design_id=design_id,
                    epoch=epoch,
                    calibration_version=calibration.fps_calibs_version,
                )
                configuration.save()

            if configuration.configuration_id is None:
                raise JaegerError("Failed loading configuration.")

            self.configuration_id = configuration.configuration_id

        else:
            if self.configuration_id is None:
                raise JaegerError("Must have a configuration_id to replace.")

            if not self.ingested:
                raise JaegerError(
                    f"Configuration ID {self.configuration_id} does not exists "
                    "in opsdb and cannot be replaced."
                )

            if replace is False:
                raise JaegerError(
                    f"Configuration ID {self.configuration_id} has already "
                    "been loaded. Use replace=True to overwrite it."
                )

            with opsdb.database.atomic():
                opsdb.AssignmentToFocal.delete().where(
                    opsdb.AssignmentToFocal.configuration_id == self.configuration_id
                ).execute(opsdb.database)

                configuration = opsdb.Configuration.get_by_id(self.configuration_id)
                configuration.delete_instance()
                self.configuration_id = None

                return self.write_to_database(replace=False)

        a_data = self.fibre_data.clone()
        a_data = a_data.with_columns(cs.ends_with("focal").fill_nan(None))

        focals = []
        for row in a_data.iter_rows(named=True):
            pid = row["positioner_id"]
            hole_id = row["hole_id"]
            assigned = row["assigned"]

            if row["valid"]:
                xfocal = row["xfocal"]
                yfocal = row["yfocal"]
            else:
                xfocal = yfocal = None

            if self.design and hole_id in self.design.target_data and assigned:
                assignment_pk = self.design.target_data[hole_id]["assignment_pk"]
            else:
                assignment_pk = None

            focals.append(
                dict(
                    assignment_pk=assignment_pk,
                    xfocal=xfocal,
                    yfocal=yfocal,
                    positioner_id=pid,
                    fiber_type=row["fibre_type"].lower(),
                    configuration_id=self.configuration_id,
                    catalogid=row["catalogid"],
                    assigned=assigned,
                )
            )

        with opsdb.database.atomic():
            opsdb.AssignmentToFocal.insert_many(focals).execute(opsdb.database)

    @staticmethod
    def _get_summary_file_path(
        configuration_id: int,
        observatory: str,
        flavour: str,
        test: bool = False,
    ):
        """Returns the path for a configuration file in ``SDSSCORE_DIR``."""

        if configuration_id is None:
            raise JaegerError("Configuration ID not set.")

        if "SDSSCORE_DIR" not in os.environ:
            raise JaegerError("$SDSSCORE_DIR is not set. Cannot write summary file.")

        sdsscore_dir = os.environ["SDSSCORE_DIR"]
        sdsscore_test_dir = os.environ.get("SDSSCORE_TEST_DIR", "")
        path = os.path.join(
            sdsscore_dir if test is False else sdsscore_test_dir,
            observatory.lower(),
            "summary_files",
            f"{int(configuration_id / 1000):03d}XXX" if test else "",
            f"{int(configuration_id / 100):04d}XX",
            f"confSummary{flavour}-{configuration_id}.par",
        )

        return path

    def write_summary(
        self,
        path: str | pathlib.Path | None = None,
        flavour: str = "",
        overwrite: bool = False,
        headers: dict = {},
        fibre_data: polars.DataFrame | None = None,
        write_confSummary_test: bool = True,
    ):
        """Writes the confSummary file."""

        # TODO: some time may be saved by doing a single DB query and retrieving
        # all the info at once for all the assignments. Need to be careful
        # to maintain the order.

        if self.configuration_id is None:
            raise JaegerError("Configuration needs to be set and loaded to the DB.")

        adata = self.assignment

        fdata = fibre_data if fibre_data is not None else self.fibre_data.clone()
        fdata = fdata.with_columns(cs.numeric().fill_null(-999).fill_nan(-999))

        design = self.design

        header = {
            "configuration_id": self.configuration_id,
            "robostrategy_run": "NA",
            "fps_calibrations_version": calibration.fps_calibs_version,
            "jaeger_version": jaeger_version,
            "coordio_version": coordio_version,
            "kaiju_version": kaiju_version,
            "design_id": self.design_id,
            "field_id": -999,
            "focal_scale": adata.scale or 0.999882,
            "instruments": "BOSS APOGEE",
            "epoch": adata.site.time.jd if adata.site.time else -999,
            "obstime": Time.now().strftime("%a %b %d %H:%M:%S %Y"),
            "MJD": get_sjd(adata.observatory.upper()),
            "observatory": adata.observatory,
            "temperature": -999.0,
            "raCen": -999.0,
            "decCen": -999.0,
            "pa": -999.0,
            "is_dithered": 0,
            "parent_configuration": -999,
            "dither_radius": -999.0,
            "cloned_from": -999,
        }

        if design:
            header.update(
                {
                    "robostrategy_run": design.field.rs_run,
                    "field_id": design.field.field_id,
                    "raCen": design.field.racen,
                    "decCen": design.field.deccen,
                    "pa": design.field.position_angle,
                }
            )

        header.update(headers)
        header.update(self.extra_summary_data)

        fibermap, default = get_fibermap_table(len(fdata))

        i = 0
        for row_data in fdata.iter_rows(named=True):
            pid = row_data["positioner_id"]
            fibre_type = row_data["fibre_type"]

            hole_id = row_data["hole_id"]

            # Start with the default row.
            row = default.copy()

            if fibre_type.upper() == "APOGEE":
                spec_id = 2
            elif fibre_type.upper() == "BOSS":
                spec_id = 1
            else:
                spec_id = -1

            # Update data that is valid for all fibres.
            row.update(
                {
                    "positionerId": pid,
                    "holeId": hole_id,
                    "fiberType": fibre_type.upper(),
                    "assigned": int(row_data["assigned"]),
                    "valid": int(row_data["valid"]),
                    "on_target": int(row_data["on_target"]),
                    "xwok": row_data["xwok"],
                    "ywok": row_data["ywok"],
                    "zwok": row_data["zwok"],
                    "xFocal": row_data["xfocal"],
                    "yFocal": row_data["yfocal"],
                    "alpha": row_data["alpha"],
                    "beta": row_data["beta"],
                    "ra": row_data["ra_epoch"],
                    "dec": row_data["dec_epoch"],
                    "ra_observed": row_data["ra_observed"],
                    "dec_observed": row_data["dec_observed"],
                    "alt_observed": row_data["alt_observed"],
                    "az_observed": row_data["az_observed"],
                    "spectrographId": spec_id,
                    "fiberId": row_data["fibre_id"],
                    "lambda_eff": row_data["wavelength"],
                }
            )

            # And now only the one that is associated with a target.
            if row_data["assigned"] and design and hole_id in design.target_data:
                target = design.target_data[hole_id]
                row.update(
                    {
                        "racat": target["ra"],
                        "deccat": target["dec"],
                        "pmra": target["pmra"] or -999.0,
                        "pmdec": target["pmdec"] or -999.0,
                        "parallax": target["parallax"] or -999.0,
                        "coord_epoch": target["epoch"] or -999.0,
                        "lambda_design": target["lambda_eff"] or -999.0,
                        "catalogid": target["catalogid"] or -999.0,
                        "carton_to_target_pk": target["carton_to_target_pk"] or -999.0,
                        "cadence": target["cadence"] or "",
                        "firstcarton": target["carton"] or "",
                        "program": target["program"] or "",
                        "category": target["category"] or "",
                        "delta_ra": target["delta_ra"] or 0.0,
                        "delta_dec": target["delta_dec"] or 0.0,
                    }
                )

                optical_mag = [target[m] or -999.0 for m in ["g", "r", "i", "z"]]
                optical_mag = [-999.0] + optical_mag  # u band
                row.update(
                    {
                        "mag": optical_mag,
                        "optical_prov": target["optical_prov"] or "",
                        "bp_mag": target["bp"] or -999.0,
                        "gaia_g_mag": target["gaia_g"] or -999.0,
                        "rp_mag": target["rp"] or -999.0,
                        "h_mag": target["h"] or -999.0,
                    }
                )

            fibermap[i] = tuple(row.values())

            i += 1

        fibermap = Table(fibermap)
        fibermap.sort(["positionerId", "fiberType"])

        if path is None:
            path = self._get_summary_file_path(
                self.configuration_id,
                self.assignment.observatory,
                flavour,
            )
        else:
            write_confSummary_test = False  # Do not write test file if path is custom.

        path = os.path.expandvars(os.path.expanduser(str(path)))

        if os.path.exists(path):
            if overwrite:
                warn(f"Summary file {os.path.basename(path)} exists. Overwriting it.")
                os.remove(path)
            else:
                raise JaegerError(f"Summary file {os.path.basename(path)} exists.")

        os.makedirs(os.path.dirname(path), exist_ok=True)

        write_ndarray_to_yanny(
            str(path),
            [fibermap],
            structnames=["FIBERMAP"],
            hdr=header,
            enums={"fiberType": ("FIBERTYPE", ("BOSS", "APOGEE", "METROLOGY", "NONE"))},
        )

        self._summary_file = str(path)

        # This is a test for now, but eventually we'll change to this format of
        # SDSSCORE directories.
        if write_confSummary_test and "SDSSCORE_TEST_DIR" in os.environ:
            test_path = self._get_summary_file_path(
                self.configuration_id,
                self.assignment.observatory,
                flavour,
                test=True,
            )
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            shutil.copyfile(path, test_path)

        return path


class Configuration(BaseConfiguration[Assignment]):
    """A configuration based on a target design."""

    def __init__(
        self,
        design: Design,
        fps: FPS | None = None,
        epoch: float | None = None,
        scale: float | None = None,
        boss_wavelength: float | None = None,
        apogee_wavelength: float | None = None,
    ):
        super().__init__(fps=fps, scale=scale)

        self.design = design
        self.design_id = design.design_id
        self.assignment = Assignment(
            self,
            epoch=epoch,
            scale=scale,
            boss_wavelength=boss_wavelength,
            apogee_wavelength=apogee_wavelength,
        )

        assert self.assignment.site.time

        self.epoch = self.assignment.site.time.jd
        self.scale = scale

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id} "
            f"design_id={self.design_id})>"
        )


class DitheredConfiguration(BaseConfiguration[Assignment]):
    """A positioner configuration dithered from a parent configuration."""

    def __init__(
        self,
        parent: BaseConfiguration,
        radius: float,
        fps: FPS | None = None,
        epoch: float | None = None,
    ):
        assert parent.design

        super().__init__(fps=fps, scale=parent.scale)

        # This needs to be set after the __init__ beccause __init__ sets parent=None.
        self.parent_configuration = parent
        assert self.parent_configuration.design

        self.is_dither = True

        self.design = self.parent_configuration.design
        self.design_id = self.design.design_id

        self.assignment = Assignment(
            self,
            epoch=epoch or self.parent_configuration.epoch,
            compute_coordinates=False,
            scale=self.scale,
        )

        self.assignment.fibre_data = parent.assignment.fibre_data.clone()
        self.assignment.site.set_time(parent.epoch)

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.assignment.boresight = Observed(
            icrs_bore,
            site=self.assignment.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        self.radius = radius

        self.extra_summary_data = {
            "parent_configuration": parent.configuration_id or -999,
            "is_dithered": 1,
            "dither_radius": radius,
        }

    async def get_paths(self, collision_buffer: float | None = None):
        """Get trajectory paths."""

        self.robot_grid = self._initialise_grid(collision_buffer=collision_buffer)

        fps = self.fps or FPS.get_instance()

        await fps.update_position()
        positions = fps.get_positions_dict()

        for robot in self.robot_grid.robotDict.values():
            if robot.isOffline:
                continue

            robot.setAlphaBeta(*positions[robot.id])
            new_alpha, new_beta = robot.uniformDither(self.radius)
            robot.setDestinationAlphaBeta(new_alpha, new_beta)

        (
            self.to_destination,
            self.from_destination,
            *_,
        ) = await get_path_pair_in_executor(
            self.robot_grid,
            path_generation_mode="greedy",
            ignore_did_fail=True,
            stop_if_deadlock=True,
            ignore_initial_collisions=True,
        )

        if self.to_destination is None or self.from_destination is None:
            raise ValueError("Failed generating to and from destination paths.")

        # Get the actual last points where we went. Due to deadlocks and
        # collisions these may not actually be the ones we set.
        new_positions: dict[int, dict[Literal["alpha", "beta"], float | None]] = {}
        for positioner_id in self.robot_grid.robotDict:
            if positioner_id in self.to_destination:
                alpha = self.to_destination[positioner_id]["alpha"][-1][0]
                beta = self.to_destination[positioner_id]["beta"][-1][0]
            else:
                alpha, beta = positions[positioner_id]

            new_positions[positioner_id] = {"alpha": alpha, "beta": beta}

        # Recompute coordinates for the new positions.
        self.assignment.update_positioner_coordinates(
            new_positions,
            validate=False,
        )

        # Unset all the measured and kaiju work coordinates.
        wok_ax: dict[str, polars.Expr] = {}
        for ax in ["x", "y", "z"]:
            wok_ax[f"{ax}wok_measured"] = polars.lit(None, dtype=polars.Float64)
            wok_ax[f"{ax}wok_kaiju"] = polars.lit(None, dtype=polars.Float64)

        self.fibre_data = self.fibre_data.with_columns(
            on_target=False,
            valid=True,
            **wok_ax,
        )

        self.assignment.validate()

        return self.to_destination


class ManualConfiguration(BaseConfiguration[ManualAssignment]):
    """A configuration create manually.

    Parameters
    ----------
    positions
        A dictionary containing the targeting information. It must be a
        mapping of hole ID to a tuple of ``alpha`` and ``beta`` positions.
    observatory
        The observatory name. If `None`, uses the value from the configuration.
    fps
        The FPS instance to use. If `None`, uses the currently running instance.
    field_centre
        A tuple or array with the boresight coordinates as current epoch
        RA/Dec. If `None`, target coordinates must be positioner or wok.
    design_id
        A design identifier for this configuration.
    position_angle
        The position angle of the field.

    """

    assignment: ManualAssignment

    def __init__(
        self,
        positions: dict[int, tuple[float | None, float | None]],
        observatory,
        fps: FPS | None = None,
        field_centre: tuple[float, float] | numpy.ndarray | None = None,
        design_id: int = -999,
        position_angle: float = 0.0,
        scale: float | None = None,
    ):
        super().__init__(fps=fps, scale=scale)

        self.design = None
        self.design_id = design_id
        self.epoch = None

        self.assignment = ManualAssignment(
            self,
            positions,
            observatory,
            field_centre=field_centre,
            position_angle=position_angle,
            scale=scale,
        )

    @classmethod
    def create_from_positions(
        cls,
        observatory: str,
        positions: dict[int, tuple[float | None, float | None]],
        **kwargs,
    ):
        """Create a manual configuration from robot positions.

        Parameters
        ----------
        observatory
            The observatory for whose FPS these positions are meant.
        positions
            A dictionary of positioner ID to a tuple of ``(alpha, beta)``.

        """

        wok_data = get_wok_data(observatory)

        for row in wok_data.iter_rows(named=True):
            positioner_id = row["positionerID"]

            if positioner_id not in positions:
                raise ValueError(f"Values for positioner {positioner_id} not provided.")

        return cls(positions, observatory, **kwargs)
