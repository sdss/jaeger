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
import warnings
from copy import deepcopy
from time import time

from typing import TYPE_CHECKING, Optional, Union, cast

import numpy
import pandas
from astropy.table import Table
from astropy.time import Time
from pydl.pydlutils.yanny import write_ndarray_to_yanny

from coordio import (
    ICRS,
    Field,
    FocalPlane,
    Observed,
    PositionerApogee,
    PositionerBoss,
    Site,
    Wok,
)
from coordio import __version__ as coordio_version
from coordio.defaults import FOCAL_SCALE, INST_TO_WAVE, POSITIONER_HEIGHT, calibration
from kaiju import __version__ as kaiju_version
from sdssdb.peewee.sdss5db import opsdb, targetdb

from jaeger import FPS
from jaeger import __version__ as jaeger_version
from jaeger import config, log
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
from jaeger.utils import get_sjd
from jaeger.utils.helpers import run_in_executor

from .tools import copy_summary_file, positioner_to_wok, wok_to_positioner


if TYPE_CHECKING:
    from clu import Command

    from jaeger.actor import JaegerActor

    from .design import Design


__all__ = [
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "AssignmentData",
    "DitheredConfiguration",
]

PositionerType = Union[PositionerApogee, PositionerBoss]


def get_fibermap_table(length: int) -> tuple[numpy.ndarray, dict]:
    """Returns a stub for the FIBERMAP table and a default entry,"""

    fiber_map_data = [
        ("positionerId", numpy.int16, -999),
        ("holeId", "U7", ""),
        ("fiberType", "U10", ""),
        ("assigned", numpy.int16, 0),
        ("on_target", numpy.int16, 0),
        ("valid", numpy.int16, 0),
        ("decollided", numpy.int16, 0),
        ("xwok", numpy.float64, -999.0),
        ("ywok", numpy.float64, -999.0),
        ("zwok", numpy.float64, -999.0),
        ("xFocal", numpy.float64, -999.0),
        ("yFocal", numpy.float64, -999.0),
        ("alpha", numpy.float32, -999.0),
        ("beta", numpy.float32, -999.0),
        ("racat", numpy.float64, -999.0),
        ("deccat", numpy.float64, -999.0),
        ("pmra", numpy.float32, -999.0),
        ("pmdec", numpy.float32, -999.0),
        ("parallax", numpy.float32, -999.0),
        ("ra", numpy.float64, -999.0),
        ("dec", numpy.float64, -999.0),
        ("lambda_eff", numpy.float32, -999.0),
        ("coord_epoch", numpy.float32, -999.0),
        ("spectrographId", numpy.int16, -999),
        ("fiberId", numpy.int16, -999),
        ("mag", numpy.dtype(("<f4", (5,))), [-999.0] * 5),
        ("optical_prov", "U30", ""),
        ("bp_mag", numpy.float32, -999.0),
        ("gaia_g_mag", numpy.float32, -999.0),
        ("rp_mag", numpy.float32, -999.0),
        ("h_mag", numpy.float32, -999.0),
        ("catalogid", numpy.int64, -999),
        ("carton_to_target_pk", numpy.int64, -999),
        ("cadence", "U100", ""),
        ("firstcarton", "U100", ""),
        ("program", "U100", ""),
        ("category", "U100", ""),
        ("sdssv_boss_target0", numpy.int64, 0),
        ("sdssv_apogee_target0", numpy.int64, 0),
        ("delta_ra", numpy.float64, 0.0),
        ("delta_dec", numpy.float64, 0.0),
    ]

    names, formats, defaults = zip(*fiber_map_data)

    fibermap = numpy.empty((length,), dtype={"names": names, "formats": formats})

    # Define a default row with all set to "" or -999. depending on column data type.
    default = {}
    for i in range(len(names)):
        name = names[i]
        default[name] = defaults[i]

    return (fibermap, default)


class BaseConfiguration:
    """A base configuration class."""

    assignment_data: BaseAssignmentData
    epoch: float | None

    def __init__(self, scale: float | None = None):

        if len(calibration.positionerTable) == 0:
            raise ValueError("FPS calibrations not loaded or the array is empty.")

        # Configuration ID is None until we insert in the database.
        # Once set, it cannot be changed.
        self.configuration_id: int | None = None
        self._summary_file: str | None = None

        self.scale = scale or FOCAL_SCALE

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

        self.fps = FPS.get_instance()

        self.robot_grid = self._initialise_grid()

        self.command: Command[JaegerActor] | None = None

        self._decollided: list[int] = []

        self.to_destination: dict | None = None
        self.from_destination: dict | None = None

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
            await new.write_summary(headers={"cloned_from": new.cloned_from})

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

    def _initialise_grid(self):

        self.robot_grid = get_robot_grid(self.fps)

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

    def recompute_coordinates(self, jd: Optional[float] = None):
        """Recalculates the coordinates.

        Parameters
        ----------
        jd
            The Julian Date for which to compute the coordinates.

        """

        if self.configuration_id is not None:
            raise JaegerError(
                "Cannot recompute coordinates once the configuration "
                "ID has been set."
            )

        self.assignment_data.compute_coordinates(jd=jd)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd

    async def get_paths(
        self,
        decollide: bool = True,
        simple_decollision: bool = False,
        resolve_deadlocks: bool = True,
        n_deadlock_retries: int = 5,
        force: bool = False,
    ) -> dict:
        """Returns a trajectory dictionary from the folded position.

        Also stores the to destination trajectory so that it can be
        used later to return to folded position.

        Parameters
        ----------
        decollide
            Runs the decollision routine.
        simple_decollision
            If `True`, runs `decollideGrid()` without trying to prioritise and
            minimise what robots move.
        resolve_deadlocks
            Whether to solve for deadlocks after decollision.
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
        self.robot_grid = self._initialise_grid()

        ftable = self.assignment_data.fibre_table
        alpha0, beta0 = config["kaiju"]["lattice_position"]

        # Assign positions to all the assigned, valid targets.
        # TODO: remove disabled from here.
        valid = ftable.loc[(ftable.assigned == 1) & (ftable.valid == 1)]

        self.log(
            f"Assigned targets {(ftable.assigned == 1).sum()}. "
            f"Valid targets {len(valid)}."
        )

        invalid = []
        for robot in self.robot_grid.robotDict.values():
            if robot.isOffline:
                ftable.loc[robot.id, "offline"] = 1
                invalid.append(robot.id)
                continue

            if robot.id not in valid.index.get_level_values(0):
                robot.setAlphaBeta(alpha0, beta0)
                robot.setDestinationAlphaBeta(alpha0, beta0)
                robot.setXYUniform()  # Scramble unassigned robots.
                invalid.append(robot.id)
            else:
                vrow = valid.loc[robot.id].iloc[0]
                robot.setAlphaBeta(vrow.alpha, vrow.beta)
                robot.setDestinationAlphaBeta(alpha0, beta0)

        self._update_coordinates(invalid)

        decollided: list[int] = []
        if decollide:
            priority_order = valid.index.get_level_values(0).tolist()

            self.robot_grid, decollided = await decollide_in_executor(
                self.robot_grid,
                simple=simple_decollision,
                priority_order=priority_order,
            )

            if len(decollided) > 0:
                self.log(
                    f"{len(decollided)} positioners were collided and "
                    f"were reassigned: {decollided}.",
                    level=logging.WARNING,
                    to_command=False,
                )

            self._update_coordinates(decollided)

            # Final check for collisions.
            if len(self.robot_grid.getCollidedRobotList()) > 0:
                raise TrajectoryError("The robot grid remains collided.")

        # Fix deadlocks (this sets the trajectories in the instance).
        self.log("Generating path pair.")
        n_retries = n_deadlock_retries if resolve_deadlocks else -1
        unlocked = await self._resolve_deadlocks(n_retries=n_retries, force=force)

        self._update_coordinates(unlocked)

        self._decollided = list(set(decollided + unlocked))
        self.assignment_data.fibre_table.loc[self._decollided, "decollided"] = 1

        if self.from_destination is None:
            raise TrajectoryError("Cannot find valid trajectory.")

        return self.from_destination

    async def _resolve_deadlocks(
        self,
        n_retries: int = 5,
        force: bool = False,
    ) -> list[int]:
        """Iteratively fix deadlocks."""

        # Save the grid data in case we need to decollide.
        grid_data = dump_robot_grid(self.robot_grid)

        attempt: int = 0
        decollided: list[int] = []

        while True:
            result = await get_path_pair_in_executor(self.robot_grid)
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

    def _update_coordinates(
        self,
        positioner_ids: list[int] = [],
        mark_off_target: bool = True,
    ):
        """Updates the coordinates of a series of robots."""

        ftable = self.assignment_data.fibre_table

        n_positioners = len(positioner_ids)
        if n_positioners > 0:
            self.log(f"Recomputing {n_positioners} coordinates.", level=logging.DEBUG)

        # Update the [xyz]wok_kaiju columns with the values the kaiju uses.
        # These should (!) be identical to [xyz]wok. We do this for all the
        # positioners in the grid.
        for robot in self.robot_grid.robotDict.values():
            cols = ["xwok_kaiju", "ywok_kaiju", "zwok_kaiju"]
            ftable.loc[(robot.id, "APOGEE"), cols] = robot.apWokXYZ
            ftable.loc[(robot.id, "BOSS"), cols] = robot.bossWokXYZ
            ftable.loc[(robot.id, "Metrology"), cols] = robot.metWokXYZ

            # If the robot is in the list it means now it's at a different position
            # now, so we need to update its coordinates in the table.
            if robot.id in positioner_ids:
                for ftype in ["APOGEE", "BOSS", "Metrology"]:
                    self.assignment_data.positioner_to_icrs(
                        robot.id,
                        ftype,
                        robot.alpha,
                        robot.beta,
                        self.design.field.position_angle if self.design else 0.0,
                        update=True,
                    )

        self.assignment_data.validate()

        if mark_off_target:
            ftable.loc[positioner_ids, "on_target"] = 0

    async def save_snapshot(self, highlight=None):
        """Saves a snapshot of the current robot grid."""

        mjd = int(Time.now().mjd)
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

        if "admin" in targetdb.database._config:
            targetdb.database.become_admin()

        assert self.assignment_data.site.time
        epoch = self.assignment_data.site.time.jd

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

        a_data = self.assignment_data.fibre_table

        focals = []
        for index, data in a_data.iterrows():

            index = cast(tuple, index)
            pid = index[0]
            hole_id = data.hole_id
            try:
                if data.valid == 0:
                    raise ValueError(f"Invalid coordinate found for positioner {pid}.")

                xfocal, yfocal = data.xfocal, data.yfocal
                if xfocal == -999.0 or yfocal == -999.0:
                    xfocal = yfocal = None

            except ValueError:
                xfocal = yfocal = None

            if self.design and hole_id in self.design.target_data:
                assignment_pk = self.design.target_data[hole_id]["assignment_pk"]
            else:
                assignment_pk = None

            focals.append(
                dict(
                    assignment_pk=assignment_pk,
                    xfocal=xfocal,
                    yfocal=yfocal,
                    positioner_id=pid,
                    configuration_id=self.configuration_id,
                )
            )

        with opsdb.database.atomic():
            opsdb.AssignmentToFocal.insert_many(focals).execute(opsdb.database)

    @staticmethod
    def _get_summary_file_path(configuration_id: int, observatory: str, flavour: str):

        if configuration_id is None:
            raise JaegerError("Configuration ID not set.")

        if "SDSSCORE_DIR" not in os.environ:
            raise JaegerError("$SDSSCORE_DIR is not set. Cannot write summary file.")

        sdsscore_dir = os.environ["SDSSCORE_DIR"]
        path = os.path.join(
            sdsscore_dir,
            observatory.lower(),
            "summary_files",
            f"{int(configuration_id / 100):04d}XX",
            f"confSummary{flavour}-{configuration_id}.par",
        )

        return path

    async def write_summary(
        self,
        flavour: str = "",
        overwrite: bool = False,
        headers: dict = {},
        fibre_table: pandas.DataFrame | None = None,
    ):
        """Writes the confSummary file."""

        # TODO: some time may be saved by doing a single DB query and retrieving
        # all the info at once for all the assignments. Need to be careful
        # to maintain the order.

        if self.configuration_id is None:
            raise JaegerError("Configuration needs to be set and loaded to the DB.")

        adata = self.assignment_data
        fdata = fibre_table or self.assignment_data.fibre_table.copy()

        # Add fiberId
        fass = pandas.merge(
            calibration.positionerTable,
            calibration.fiberAssignments,
            left_index=True,
            right_index=True,
        ).set_index("positionerID_x")

        fdata.loc[(fass.index, "APOGEE"), "fiberId"] = fass.APOGEEFiber.tolist()
        fdata.loc[(fass.index, "BOSS"), "fiberId"] = fass.BOSSFiber.tolist()

        fdata.fillna(-999, inplace=True)

        time = Time.now()

        design = self.design

        if self.fps and isinstance(self.fps.ieb, IEB):
            temp: float = (await self.fps.ieb.read_device("T3"))[0]
        else:
            temp = -999.0

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
            "obstime": time.strftime("%a %b %d %H:%M:%S %Y"),
            "MJD": int(get_sjd(adata.observatory.upper())),
            "observatory": adata.observatory,
            "temperature": round(temp, 1),
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
        for index, row_data in fdata.iterrows():

            index = cast(tuple, index)

            pid, fibre_type = index
            hole_id = row_data.hole_id

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
                    "assigned": row_data.assigned,
                    "valid": row_data.valid,
                    "on_target": row_data.on_target,
                    "xwok": row_data.xwok,
                    "ywok": row_data.ywok,
                    "zwok": row_data.zwok,
                    "xFocal": row_data.xfocal,
                    "yFocal": row_data.yfocal,
                    "alpha": row_data.alpha,
                    "beta": row_data.beta,
                    "ra": row_data.ra_epoch,
                    "dec": row_data.dec_epoch,
                    "spectrographId": spec_id,
                    "fiberId": row_data.fiberId,
                }
            )

            # And now only the one that is associated with a target.
            if row_data.assigned == 1 and design and hole_id in design.target_data:
                target = design.target_data[hole_id]
                row.update(
                    {
                        "racat": target["ra"],
                        "deccat": target["dec"],
                        "pmra": target["pmra"] or -999.0,
                        "pmdec": target["pmdec"] or -999.0,
                        "parallax": target["parallax"] or -999.0,
                        "coord_epoch": target["epoch"] or -999.0,
                        "lambda_eff": target["lambda_eff"] or -999.0,
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

        path = self._get_summary_file_path(
            self.configuration_id,
            self.assignment_data.observatory,
            flavour,
        )

        if os.path.exists(path):
            if overwrite:
                warn(f"Summary file {os.path.basename(path)} exists. Overwriting it.")
                os.remove(path)
            else:
                raise JaegerError(f"Summary file {os.path.basename(path)} exists.")

        os.makedirs(os.path.dirname(path), exist_ok=True)

        write_ndarray_to_yanny(
            path,
            [fibermap],
            structnames=["FIBERMAP"],
            hdr=header,
            enums={"fiberType": ("FIBERTYPE", ("BOSS", "APOGEE", "METROLOGY", "NONE"))},
        )

        self._summary_file = str(path)

        return path


class Configuration(BaseConfiguration):
    """A configuration based on a target design."""

    assignment_data: AssignmentData

    def __init__(
        self,
        design: Design,
        epoch: float | None = None,
        scale: float | None = None,
    ):

        super().__init__(scale=scale)

        self.design = design
        self.design_id = design.design_id
        self.assignment_data = AssignmentData(self, epoch=epoch, scale=scale)

        assert self.assignment_data.site.time

        self.epoch = self.assignment_data.site.time.jd
        self.scale = scale

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id} "
            f"design_id={self.design_id})>"
        )


class DitheredConfiguration(BaseConfiguration):
    """A positioner configuration dithered from a parent configuration."""

    def __init__(
        self,
        configuration: BaseConfiguration,
        radius: float,
        epoch: float | None = None,
    ):

        assert configuration.design

        super().__init__(scale=configuration.scale)

        self.parent_configuration: BaseConfiguration = configuration
        self.is_dither = True

        self.design = configuration.design
        self.design_id = self.design.design_id

        self.assignment_data = AssignmentData(
            self,
            epoch=epoch,
            computer_coordinates=False,
            scale=self.scale,
        )
        self.assignment_data.fibre_table = (
            self.parent_configuration.assignment_data.fibre_table.copy()
        )

        self.assignment_data.site.set_time(epoch)

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.assignment_data.boresight = Observed(
            icrs_bore,
            site=self.assignment_data.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        assert self.assignment_data.site.time

        self.epoch = self.assignment_data.site.time.jd

        self.radius = radius

        self.extra_summary_data = {
            "parent_configuration": self.parent_configuration.configuration_id or -999,
            "is_dithered": 1,
            "dither_radius": radius,
        }

    def compute_coordinates(self, new_positions: dict):

        assert self.design

        ftable = self.assignment_data.fibre_table

        data = {}

        for index, _ in self.assignment_data.fibre_table.iterrows():
            pid, ftype = cast(tuple, index)

            new_alpha = new_positions[pid]["alpha"]
            new_beta = new_positions[pid]["beta"]

            row_data = self.assignment_data.positioner_to_icrs(
                pid,
                ftype,
                new_alpha,
                new_beta,
                position_angle=self.design.field.position_angle,
                update=False,
            )

            data[(pid, ftype)] = row_data

        # Now do a single update of the whole fibre table.
        new_fibre_table = pandas.DataFrame.from_dict(data, orient="index")
        new_fibre_table.sort_index(inplace=True)
        new_fibre_table.index.set_names(("positioner_id", "fibre_type"), inplace=True)

        # Copy some of the coordinate columns back to the original table.
        cols = [
            "ra_epoch",
            "dec_epoch",
            "az",
            "alt",
            "xfocal",
            "yfocal",
            "xwok",
            "ywok",
            "zwok",
            "xtangent",
            "ytangent",
            "ztangent",
            "alpha",
            "beta",
        ]
        ftable.loc[new_fibre_table.index, cols] = new_fibre_table.loc[:, cols]

        for ax in ["x", "y", "z"]:
            ftable.loc[:, f"{ax}wok_measured"] = numpy.nan
            ftable.loc[:, f"{ax}wok_kaiju"] = numpy.nan

        ftable.loc[:, "on_target"] = 0

        # Reset all to valid, then validate.
        ftable.loc[:, "valid"] = 1
        self.assignment_data.validate()

    async def get_paths(self):

        self.robot_grid = self._initialise_grid()

        await self.fps.update_position()
        positions = self.fps.get_positions_dict()

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
            ignore_did_fail=True,
            stop_if_deadlock=True,
            ignore_initial_collisions=True,
        )

        # Get the actual last points where we went. Due to deadlocks and
        # collisions these may not actually be the ones we set.
        new_positions = {}
        for positioner_id in self.robot_grid.robotDict:
            if positioner_id in self.to_destination:
                alpha = self.to_destination[positioner_id]["alpha"][-1][0]
                beta = self.to_destination[positioner_id]["beta"][-1][0]
            else:
                alpha, beta = positions[positioner_id]

            new_positions[positioner_id] = {"alpha": alpha, "beta": beta}

        self.compute_coordinates(new_positions)

        return self.to_destination


class ManualConfiguration(BaseConfiguration):
    """A configuration create manually.

    Parameters
    ----------
    target_data
        A dictionary containing the targeting information. It must be a
        mapping of hole ID to dictionary. The hole ID dictionaries must
        include one of the following pairs: ``ra_icrs`` and ``dec_icrs``,
        ``xwok`` and ywok``, or ``alpha`` and ``beta``, in order of priority.
        The remaining coordinates will be filled out using coordinate
        transformations. If ``ra_icrs/dec_icrs`` are passed, a value for
        ``epoch`` is also required. An additional key, ``fibre_type``
        must be set for each target with value ``'APOGEE'``, ``'BOSS'``,
        or ``'Metrology``.
    field_centre
        A tuple or array with the boresight coordinates as current epoch
        RA/Dec. If `None`, target coordinates must be positioner or wok.
    design_id
        A design identifier for this configuration.
    observatory
        The observatory name. If `None`, uses the value from the configuration.
    position_angle
        The position angle of the field.

    """

    assignment_data: ManualAssignmentData

    def __init__(
        self,
        target_data: dict[str, dict],
        field_centre: tuple[float, float] | numpy.ndarray | None = None,
        design_id: int = -999,
        observatory: str | None = None,
        position_angle: float = 0.0,
        scale: float | None = None,
    ):

        super().__init__(scale=scale)

        self.design = None
        self.design_id = design_id
        self.epoch = None

        if observatory is None:
            if config["observatory"] != "${OBSERATORY}":
                observatory = config["observatory"]
                assert isinstance(observatory, str)
            else:
                raise ValueError("Unknown site.")

        self.target_data = target_data
        self.assignment_data = ManualAssignmentData(
            self,
            target_data,
            observatory,
            field_centre=field_centre,
            position_angle=position_angle,
            scale=scale,
        )

    @classmethod
    def create_from_positions(cls, positions, **kwargs):
        """Create a manual configuration from robot positions.

        Parameters
        ----------
        positions
            A dictionary of positioner ID to a tuple of ``(alpha, beta)``.

        """

        positionerTable = calibration.positionerTable.reset_index()
        data = {}

        for _, row in positionerTable.iterrows():
            hole_id = row.holeID
            positioner_id = row.positionerID

            if positioner_id not in positions:
                raise ValueError(f"Values for positioner {positioner_id} not provided.")

            alpha, beta = positions[positioner_id]
            data[hole_id] = {"alpha": alpha, "beta": beta, "fibre_type": "Metrology"}

        return cls(data, **kwargs)


class BaseAssignmentData:
    """Information about the target assignment along with coordinate transformation."""

    boresight: Observed
    position_angle: float

    _columns = [
        ("positioner_id", numpy.int32, None),
        ("fibre_type", "U10", None),
        ("hole_id", "U10", ""),
        ("assigned", numpy.int8, 0),
        ("valid", numpy.int8, 1),
        ("on_target", numpy.int8, 0),
        ("disabled", numpy.int8, 0),
        ("offline", numpy.int8, 0),
        ("deadlocked", numpy.int8, 0),
        ("decollided", numpy.int8, 0),
        ("dubious", numpy.int8, 0),
        ("wavelength", numpy.float32, numpy.nan),
        ("fiberId", numpy.int32, -999),
        ("ra_icrs", numpy.float64, numpy.nan),
        ("dec_icrs", numpy.float64, numpy.nan),
        ("ra_epoch", numpy.float64, numpy.nan),
        ("dec_epoch", numpy.float64, numpy.nan),
        ("alt", numpy.float64, numpy.nan),
        ("az", numpy.float64, numpy.nan),
        ("xfocal", numpy.float64, numpy.nan),
        ("yfocal", numpy.float64, numpy.nan),
        ("xwok", numpy.float64, numpy.nan),
        ("ywok", numpy.float64, numpy.nan),
        ("zwok", numpy.float64, numpy.nan),
        ("xwok_kaiju", numpy.float64, numpy.nan),
        ("ywok_kaiju", numpy.float64, numpy.nan),
        ("zwok_kaiju", numpy.float64, numpy.nan),
        ("xwok_measured", numpy.float64, numpy.nan),
        ("ywok_measured", numpy.float64, numpy.nan),
        ("zwok_measured", numpy.float64, numpy.nan),
        ("xtangent", numpy.float64, numpy.nan),
        ("ytangent", numpy.float64, numpy.nan),
        ("ztangent", numpy.float64, numpy.nan),
        ("alpha", numpy.float64, numpy.nan),
        ("beta", numpy.float64, numpy.nan),
    ]

    def __init__(
        self,
        configuration: Configuration | ManualConfiguration | DitheredConfiguration,
        observatory: Optional[str] = None,
        scale: float | None = None,
    ):

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.configuration.design_id

        self.observatory: str
        if observatory is None:
            if self.design is None:
                raise ValueError("Cannot determine observatory.")
            self.observatory = self.design.field.observatory.upper()
        else:
            self.observatory = observatory

        self.site = Site(self.observatory)
        self.site.set_time()

        self.scale = scale or FOCAL_SCALE

        positionerTable = calibration.positionerTable
        wokCoords = calibration.wokCoords
        if positionerTable is None or wokCoords is None:
            raise ValueError("FPS calibrations not loaded.")

        self.wok_data = pandas.merge(
            positionerTable.reset_index(),
            wokCoords.reset_index(),
            on="holeID",
        )
        self.wok_data.set_index("positionerID", inplace=True)

        if self.design:
            self.target_data = self.design.target_data
            self.position_angle = self.design.field.position_angle
        else:
            self.target_data = {}

        names, _, values = zip(*self._columns)
        self._defaults = {
            name: values[i] for i, name in enumerate(names) if values[i] is not None
        }

        self.fibre_table = self._create_fibre_table()

    def __repr__(self):
        return f"<{self.__class__.__name__} (design_id={self.design_id})>"

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        jd
            The Julian Date for which to compute the coordinates.

        """

        raise NotImplementedError("Must be overridden by subclasses.")

    def _create_fibre_table(self):
        """Creates an empty fibre table."""

        names, dtypes, defaults = zip(*self._columns)

        # Create empty dataframe with zero values. Fill out all the index data.
        npositioner = len(self.wok_data)
        base = numpy.zeros((npositioner * 3,), dtype=list(zip(names, dtypes)))

        for i in range(len(defaults)):
            if defaults[i] is None:
                continue
            base[names[i]] = defaults[i]

        i = 0
        for pid in self.wok_data.index.tolist():
            for ft in ["APOGEE", "BOSS", "Metrology"]:
                base["positioner_id"][i] = pid
                base["fibre_type"][i] = ft
                base["hole_id"][i] = self.wok_data.loc[pid].holeID
                i += 1

        fibre_table = pandas.DataFrame(base)

        fibre_table.fibre_type = fibre_table.fibre_type.astype("category")
        fibre_table.hole_id = fibre_table.hole_id.astype("string")

        fibre_table.set_index(["positioner_id", "fibre_type"], inplace=True)
        fibre_table = fibre_table.sort_index()

        return fibre_table

    def _init_from_icrs(self):
        """Loads fibre data from target data using ICRS coordinates."""

        alpha0, beta0 = config["kaiju"]["lattice_position"]

        data = {}
        for pid in self.wok_data.index:

            positioner_data = self.wok_data.loc[pid]
            hole_id = positioner_data.holeID

            target_fibre_type: str | None = None
            if hole_id in self.target_data:

                # First do the assigned fibre.
                ftype = self.target_data[hole_id]["fibre_type"].upper()
                target_fibre_type = ftype

                target_data = self.target_data[hole_id]
                positioner_data = self.icrs_to_positioner(
                    pid,
                    ftype,
                    target_data,
                    position_angle=self.position_angle,
                    update=False,
                    on_target=1,
                    assigned=1,
                )
                data[(pid, ftype)] = positioner_data

            else:

                # If a positioner does not have an assigned target, leave it folded.
                target_fibre_type = None
                positioner_data = {"alpha": alpha0, "beta": beta0}

            # Now calculate some coordinates for the other two non-assigned fibres.
            for ftype in ["APOGEE", "BOSS", "Metrology"]:
                if ftype == target_fibre_type:
                    continue

                icrs_data = self.positioner_to_icrs(
                    pid,
                    ftype,
                    positioner_data["alpha"],
                    positioner_data["beta"],
                    position_angle=self.position_angle,
                    update=False,
                )
                data[(pid, ftype)] = icrs_data

        # Now do a single update of the whole fibre table.
        self.fibre_table = pandas.DataFrame.from_dict(data, orient="index")
        self.fibre_table.sort_index(inplace=True)
        self.fibre_table.index.set_names(("positioner_id", "fibre_type"), inplace=True)

    def _init_from_wok(self):
        """Loads fibre data from target data using wok coordinates."""

        self._check_all_assigned()

        # Calculate positioner coordinates for each wok coordinate.
        for hole_id, data in self.target_data.items():

            xwok = data["xwok"]
            ywok = data["ywok"]
            zwok = data.get("zwok", POSITIONER_HEIGHT)
            fibre_type = data["fibre_type"]

            (alpha, beta), _ = wok_to_positioner(
                hole_id,
                self.site.name,
                fibre_type,
                xwok,
                ywok,
                zwok,
            )

            self.target_data[hole_id].update({"alpha": alpha, "beta": beta})

        # Now simply call _from_positioner()
        self._init_from_positioner()

        # We want to keep the original wok coordinates that now have been overridden
        # by calling _from_positioner.
        wok_coords = []
        for idx in range(len(self.fibre_table)):
            row: pandas.Series = self.fibre_table.iloc[idx]
            hole_id = row.hole_id
            assigned = row.assigned

            if assigned == 1 and hole_id in self.target_data:
                target = self.target_data[hole_id]
                wok_coords.append(
                    [
                        target["xwok"],
                        target["ywok"],
                        target.get("zwok", numpy.nan),
                    ]
                )
            else:
                wok_coords.append([numpy.nan] * 3)

        self.fibre_table[["xwok", "ywok", "zwok"]] = wok_coords

    def _init_from_positioner(self):
        """Loads fibre data from target data using positioner coordinates."""

        self._check_all_assigned()

        data = {}
        for pid in self.wok_data.index:

            hole_id = self.wok_data.at[pid, "holeID"]
            alpha = self.target_data[hole_id]["alpha"]
            beta = self.target_data[hole_id]["beta"]

            # Now calculate some coordinates for the other two non-assigned fibres.
            for ftype in ["APOGEE", "BOSS", "Metrology"]:

                assigned = self.target_data[hole_id]["fibre_type"] == ftype

                # If boresight has been set, go all the way from positioner to ICRS.
                # Otherwise just calculate tangent and wok.
                if self.boresight:
                    icrs_data = self.positioner_to_icrs(
                        pid,
                        ftype,
                        alpha,
                        beta,
                        position_angle=self.position_angle,
                        update=False,
                        assigned=int(assigned),
                        on_target=int(assigned),
                    )
                    data[(pid, ftype)] = icrs_data

                else:

                    wok, tangent = positioner_to_wok(
                        hole_id,
                        self.site.name,
                        ftype,
                        alpha,
                        beta,
                    )

                    row = self._defaults.copy()
                    row.update(
                        {
                            "hole_id": hole_id,
                            "alpha": alpha,
                            "beta": beta,
                            "xwok": wok[0],
                            "ywok": wok[1],
                            "zwok": wok[2],
                            "xtangent": tangent[0],
                            "ytangent": tangent[1],
                            "ztangent": tangent[2],
                            "assigned": int(assigned),
                            "on_target": int(assigned),
                        }
                    )
                    data[(pid, ftype)] = row

        # Now do a single update of the whole fibre table.
        new_data = pandas.DataFrame.from_dict(data, orient="index")
        self.fibre_table.loc[new_data.index, new_data.columns] = new_data
        self.fibre_table.index.set_names(("positioner_id", "fibre_type"), inplace=True)

    def _check_all_assigned(self):
        """Check that all the positioners are in ``target_data``."""

        if len(set(self.target_data) - set(self.wok_data.holeID)) > 0:
            raise ValueError("Not all the positioners have been assigned a target.")

    def validate(self):
        """Validates the fibre table."""

        alpha_beta = self.fibre_table[["alpha", "beta"]]
        na = alpha_beta.isna().any(axis=1)
        over_180 = self.fibre_table.beta > 180

        self.fibre_table.loc[na | over_180, "valid"] = 0
        self.fibre_table.loc[na | over_180, "on_target"] = 0

    def icrs_to_positioner(
        self,
        positioner_id: int,
        fibre_type: str,
        target_data: dict,
        position_angle: float = 0.0,
        update: bool = True,
        **kwargs,
    ):
        """Converts from ICRS coordinates."""

        hole_id = self.wok_data.loc[positioner_id].holeID
        wavelength = INST_TO_WAVE.get(fibre_type.capitalize(), INST_TO_WAVE["GFA"])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            ra = target_data["ra"]
            if target_data["delta_ra"] is not None:
                # delta_ra/delta_dec are in arcsec.
                cos_dec = numpy.cos(numpy.deg2rad(target_data["dec"]))
                ra += target_data["delta_ra"] / 3600.0 / cos_dec

            dec = target_data["dec"]
            if target_data["delta_dec"] is not None:
                dec += target_data["delta_dec"] / 3600.0

            pmra = target_data["pmra"]
            pmdec = target_data["pmdec"]
            parallax = target_data["parallax"]
            epoch = target_data["epoch"]

            icrs = ICRS(
                [[ra, dec]],
                pmra=numpy.nan_to_num(pmra, nan=0),
                pmdec=numpy.nan_to_num(pmdec, nan=0),
                parallax=numpy.nan_to_num(parallax),
                epoch=Time(epoch, format="jyear").jd,
            )

            assert self.site.time
            icrs_epoch = icrs.to_epoch(self.site.time.jd, site=self.site)

            observed = Observed(icrs_epoch, wavelength=wavelength, site=self.site)
            field = Field(observed, field_center=self.boresight)
            focal = FocalPlane(
                field,
                wavelength=wavelength,
                site=self.site,
                fpScale=self.scale,
            )
            wok = Wok(focal, site=self.site, obsAngle=position_angle)

            positioner, tangent = wok_to_positioner(
                hole_id,
                self.site.name,
                fibre_type,
                wok[0][0],
                wok[0][1],
                wok[0][2],
            )

        if update is False:
            row = self._defaults.copy()
        else:
            row = {}

        row.update(
            {
                "hole_id": hole_id,
                "wavelength": INST_TO_WAVE[fibre_type.capitalize()],
                "ra_icrs": icrs[0, 0],
                "dec_icrs": icrs[0, 1],
                "ra_epoch": icrs_epoch[0, 0],
                "dec_epoch": icrs_epoch[0, 1],
                "alt": observed[0, 0],
                "az": observed[0, 1],
                "xfocal": focal[0, 0],
                "yfocal": focal[0, 1],
                "xwok": wok[0, 0],
                "ywok": wok[0, 1],
                "zwok": wok[0, 2],
                "xtangent": tangent[0],
                "ytangent": tangent[1],
                "ztangent": tangent[2],
                "alpha": positioner[0],
                "beta": positioner[1],
            }
        )
        row.update(kwargs)

        if update:
            self.fibre_table.loc[(positioner_id, fibre_type), row.keys()] = row

        return row

    def positioner_to_icrs(
        self,
        positioner_id: int,
        fibre_type: str,
        alpha: float,
        beta: float,
        position_angle: float | None = None,
        update: bool = True,
        **kwargs,
    ):
        """Converts from positioner to ICRS coordinates."""

        wavelength = INST_TO_WAVE.get(fibre_type.capitalize(), INST_TO_WAVE["GFA"])

        assert self.site.time

        if position_angle is None:
            position_angle = self.design.field.position_angle if self.design else 0.0

        hole_id = self.wok_data.at[positioner_id, "holeID"]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            wok, tangent = positioner_to_wok(
                hole_id,
                self.site.name,
                fibre_type,
                alpha,
                beta,
            )

            focal = FocalPlane(
                Wok([wok], site=self.site, obsAngle=position_angle),
                wavelength=wavelength,
                site=self.site,
                fpScale=self.scale,
            )

            if self.boresight is not None:
                field = Field(focal, field_center=self.boresight)
                obs = Observed(field, site=self.site, wavelength=wavelength)
                icrs = ICRS(obs, epoch=self.site.time.jd)
            else:
                field = obs = icrs = None

        if update is False:
            row = self._defaults.copy()
        else:
            row = {}

        row.update(
            {
                "hole_id": hole_id,
                "wavelength": wavelength,
                "ra_epoch": icrs[0, 0] if icrs is not None else numpy.nan,
                "dec_epoch": icrs[0, 1] if icrs is not None else numpy.nan,
                "xfocal": focal[0, 0],
                "yfocal": focal[0, 1],
                "xwok": wok[0],
                "ywok": wok[1],
                "zwok": wok[2],
                "alpha": alpha,
                "beta": beta,
                "xtangent": tangent[0],
                "ytangent": tangent[1],
                "ztangent": tangent[2],
            }
        )
        row.update(kwargs)

        if update:
            self.fibre_table.loc[(positioner_id, fibre_type), row.keys()] = row

        return row


class AssignmentData(BaseAssignmentData):
    """Assignment data from a valid design with associated target information."""

    design: Design

    def __init__(
        self,
        configuration: Configuration | DitheredConfiguration,
        epoch: float | None = None,
        computer_coordinates: bool = True,
        scale: float | None = None,
    ):

        super().__init__(configuration, scale=scale)

        if computer_coordinates:
            self.compute_coordinates(epoch)

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        jd
            The Julian Date for which to compute the coordinates.
        positioner_ids
            If `None`, compute coordinates for all the entries in the fibre table.
            Otherwise, only update the entries for the positioner IDs listed.

        """

        self.site.set_time(jd)

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        # Load fibre data using ICRS coordinates.
        self._init_from_icrs()

        # Final validation
        self.validate()


class ManualAssignmentData(BaseAssignmentData):
    """Assignment data from a manual configuration.

    Parameters
    ----------
    configuration
        The parent `.ManualConfiguration`.
    target_data
        A dictionary containing the targeting information. It must be a
        mapping of hole ID to dictionary. The hole ID dictionaries must
        include one of the following pairs: ``ra_icrs`` and ``dec_icrs``,
        ``xwok`` and ywok``, or ``alpha`` and ``beta``, in order of priority.
        The remaining coordinates will be filled out using coordinate
        transformations. If ``ra_icrs/dec_icrs`` are passed, a value for
        ``epoch`` is also required. An additional key, ``fibre_type``
        must be set for each target with value ``'APOGEE'``, ``'BOSS'``,
        or ``'Metrology``.
    observatory
        The observatory name. If `None`, uses the value from the configuration.
    field_centre
        A tuple or array with the boresight coordinates as current epoch
        RA/Dec. If `None`, target data must be positioner or wok coordinates.
    position_angle
        The position angle of the field.
    scale
        The focal plane scale factor.

    """

    boresight: Observed | None = None

    def __init__(
        self,
        configuration: ManualConfiguration,
        target_data: dict,
        observatory: str,
        field_centre: tuple[float, float] | numpy.ndarray | None = None,
        position_angle: float = 0.0,
        scale: float | None = None,
    ):

        super().__init__(configuration, observatory=observatory, scale=scale)

        self.target_data = target_data

        if field_centre is not None:
            self.field_centre = numpy.array(field_centre)
        else:
            self.field_centre = None

        self.position_angle = position_angle

        self.fibre_table = self._create_fibre_table()
        self.compute_coordinates()

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        jd
            The Julian Date for which to compute the coordinates.

        """

        self.site.set_time(jd)

        if self.field_centre:
            icrs_bore = ICRS([self.field_centre])
            self.boresight = Observed(
                icrs_bore,
                site=self.site,
                wavelength=INST_TO_WAVE["GFA"],
            )

        if len(self.target_data) == 0:
            raise ValueError("Target data has zero length.")

        sample_target = list(self.target_data.values())[0]

        if "fibre_type" not in sample_target:
            raise ValueError("fibre_type column missing from target data.")

        if "ra_icrs" in sample_target and "dec_icrs" in sample_target:
            if "epoch" not in sample_target:
                raise ValueError("Missing epoch information.")
            if self.boresight is None:
                raise ValueError(
                    "Creating a manual configuration from ICRS "
                    "coordinates requires defining a field centre."
                )
            self._init_from_icrs()
        elif "xwok" in sample_target and "ywok" in sample_target:
            self._init_from_wok()
        elif "alpha" in sample_target and "beta" in sample_target:
            self._init_from_positioner()
        else:
            raise ValueError("Target data does not contain the necessary columns.")

        # Final validation.
        self.validate()
