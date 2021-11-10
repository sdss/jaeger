#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-10-13
# @Filename: design.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os
import warnings

from typing import TYPE_CHECKING, Optional, Union, cast

import numpy
import pandas
import peewee
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
    PositionerMetrology,
    Site,
    Tangent,
    Wok,
)
from coordio.defaults import (
    INST_TO_WAVE,
    fps_calibs_version,
    positionerTable,
    wokCoords,
)
from sdssdb.peewee.sdss5db import opsdb, targetdb

from jaeger import config, log
from jaeger.exceptions import JaegerError, JaegerUserWarning, TrajectoryError
from jaeger.fps import FPS
from jaeger.utils.helpers import run_in_executor


if TYPE_CHECKING:
    from kaiju import RobotGridCalib


__all__ = [
    "Design",
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "TargetAssignmentData",
    "ManualAssignmentData",
    "unwind",
    "explode",
    "get_robot_grid",
]


PositionerType = Union[PositionerApogee, PositionerBoss]


def warn(message):
    warnings.warn(message, JaegerUserWarning)


def get_robot_grid(seed: int = 0):
    """Returns a new robot grid with the destination set to the lattice position.

    If an initialised instance of the FPS is available, disabled robots will be
    set offline in the grid at their current positions.

    """

    fps = FPS.get_instance()
    if fps is None:
        warn(
            "FPS information not provided when creating the robot grid. "
            "Will not be able to disable robots."
        )

    from kaiju.robotGrid import RobotGridCalib

    kaiju_config = config["kaiju"]
    ang_step = kaiju_config["ang_step"]
    collision_buffer = kaiju_config["collision_buffer"]
    alpha0, beta0 = kaiju_config["lattice_position"]
    epsilon = ang_step * 2

    robot_grid = RobotGridCalib(
        stepSize=ang_step,
        collisionBuffer=collision_buffer,
        epsilon=epsilon,
        seed=seed,
    )

    for robot in robot_grid.robotDict.values():
        if fps:
            if robot.id not in fps.positioners:
                raise JaegerError(f"Robot {robot.id} is not connected.")
            positioner = fps[robot.id]
            if positioner.disabled:
                log.debug(f"Setting positioner {robot.id} offline in Kaiju.")
                robot.setAlphaBeta(positioner.alpha, positioner.beta)
                robot.setDestinationAlphaBeta(positioner.alpha, positioner.beta)
                robot.isOffline = True
                continue

        robot.setDestinationAlphaBeta(alpha0, beta0)

    return robot_grid


def decollide_grid(robot_grid: RobotGridCalib, simple=False):
    """Decollides a potentially collided grid. Raises on fail.

    If ``simple=True``, just runs a ``decollideGrid()`` and returns silently.

    """

    def get_collided():
        collided = [rid for rid in robot_grid.robotDict if robot_grid.isCollided(rid)]
        if len(collided) == 0:
            return False
        else:
            return collided

    if simple:
        robot_grid.decollideGrid()
        if get_collided() is not False:
            raise JaegerError("Failed decolliding grid.")
        return

    # First pass. If collided, decollide each robot one by one.
    # TODO: Probably this should be done in order of less to more important targets
    # to throw out the less critical ones first.
    collided = get_collided()
    if collided is not False:
        warn("The grid is collided. Attempting one-by-one decollision.")
        for robot_id in collided:
            if robot_grid.isCollided(robot_id):
                robot_grid.decollideRobot(robot_id)
                if robot_grid.isCollided(robot_id):
                    warn(f"Failed decolliding positioner {robot_id}.")
                else:
                    warn(f"Positioner {robot_id} was successfully decollided.")

    # Second pass. If still collided, try a grid decollision.
    if get_collided() is not False:
        warn("Grid is still colliding. Attempting full grid decollision.")
        robot_grid.decollideGrid()
        if get_collided() is not False:
            raise JaegerError("Failed decolliding grid.")
        else:
            warn("The grid was decollided.")


def unwind(current_positions: dict[int, tuple[float, float]]):
    """Folds all the robots to the lattice position."""

    robot_grid = get_robot_grid()

    for robot in robot_grid.robotDict.values():
        if robot.id not in current_positions:
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot_position = current_positions[robot.id]
        robot.setAlphaBeta(robot_position[0], robot_position[1])

    for robot in robot_grid.robotDict.values():
        if robot_grid.isCollided(robot.id):
            raise ValueError(f"Robot {robot.id} is kaiju-collided. Cannot unwind.")

    robot_grid.pathGenGreedy()
    if robot_grid.didFail:
        raise TrajectoryError(
            "Failed generating a valid trajectory. "
            "This usually means a deadlock was found."
        )

    layout_pids = [robot.id for robot in robot_grid.robotDict.values()]
    if len(set(current_positions.keys()) - set(layout_pids)) > 0:
        # Some connected positioners are not in the layout.
        raise ValueError("Some connected positioners are not in the grid layout.")

    speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]

    _, reverse = robot_grid.getPathPair(speed=speed)

    return reverse


def explode(current_positions: dict[int, tuple[float, float]], explode_deg=20.0):
    """Explodes the grid by a number of degrees."""

    robot_grid = get_robot_grid()

    for robot in robot_grid.robotDict.values():
        if robot.id not in current_positions:
            raise ValueError(f"Positioner {robot.id} is not connected.")

        robot_position = current_positions[robot.id]
        robot.setAlphaBeta(robot_position[0], robot_position[1])

    robot_grid.pathGenEscape(explode_deg)

    layout_pids = [robot.id for robot in robot_grid.robotDict.values()]
    if len(set(current_positions.keys()) - set(layout_pids)) > 0:
        # Some connected positioners are not in the layout.
        raise ValueError("Some connected positioners are not in the grid layout.")

    speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]

    _, reverse = robot_grid.getPathPair(speed=speed)

    return reverse


def get_fibermap_table() -> tuple[Table, dict]:
    """Returns a stub for the FIBERMAP table and a default entry,"""

    fiber_map_data = [
        ("positionerId", numpy.int16),
        ("holeId", "S7"),
        ("fiberType", "S10"),
        ("assigned", numpy.int16),
        ("valid", numpy.int16),
        ("xFocal", numpy.float64),
        ("yFocal", numpy.float64),
        ("alpha", numpy.float32),
        ("beta", numpy.float32),
        ("racat", numpy.float64),
        ("deccat", numpy.float64),
        ("pmra", numpy.float32),
        ("pmdec", numpy.float32),
        ("parallax", numpy.float32),
        ("ra", numpy.float64),
        ("dec", numpy.float64),
        ("lambda_eff", numpy.float32),
        ("coord_epoch", numpy.float32),
        ("spectrographId", numpy.int16),
        ("mag", numpy.dtype(("<f4", (5,)))),
        ("optical_prov", "S10"),
        ("bp_mag", numpy.float32),
        ("gaia_g_mag", numpy.float32),
        ("rp_mag", numpy.float32),
        ("h_mag", numpy.float32),
        ("catalogid", numpy.int64),
        ("carton_to_target_pk", numpy.int64),
        ("cadence", "S20"),
        ("firstcarton", "S25"),
        ("program", "S20"),
        ("category", "S20"),
        ("sdssv_boss_target0", numpy.int64),
        ("sdssv_apogee_target0", numpy.int64),
    ]

    names, dtype = zip(*fiber_map_data)

    fibermap = Table(rows=None, names=names, dtype=dtype)

    # Define a default row with all set to "" or -999. depending on column data type.
    default = {}
    for i in range(len(names)):
        name = names[i]
        dd = numpy.dtype(dtype[i])
        if name == "mag":
            value = [-999.0] * 5
        elif dd.char in ["h", "i"]:
            value = -999
        elif dd.char in ["S"]:
            value = ""
        elif dd.char in ["f", "d"]:
            value = -999.0
        else:
            value = -999.0
        default[name] = value

    default["assigned"] = 0
    default["valid"] = 0
    default["sdssv_boss_target0"] = 0
    default["sdssv_apogee_target0"] = 0

    return (fibermap, default)


class Design:
    """Loads and represents a targetdb design."""

    def __init__(self, design_id: int, load_configuration=True):

        if wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

        self.design_id = design_id

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field = self.design.field
        self.target_data: dict[str, dict] = self.get_target_data()

        self.configuration: Configuration
        if load_configuration:
            self.configuration = Configuration(self)

    def get_target_data(self) -> dict[str, dict]:
        """Retrieves target data as a dictionary."""

        # TODO: this is all synchronous which is probably ok because this
        # query should run in < 1s, but at some point maybe we can change
        # this to use async-peewee and aiopg.

        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")

        target_data = (
            targetdb.Design.select(
                targetdb.Assignment.pk.alias("assignment_pk"),
                targetdb.CartonToTarget.pk.alias("carton_to_target_pk"),
                targetdb.CartonToTarget.lambda_eff,
                targetdb.Target,
                targetdb.Magnitude,
                targetdb.Hole.holeid,
                targetdb.Instrument.label.alias("fibre_type"),
                targetdb.Cadence.label.alias("cadence"),
                targetdb.Carton.carton,
                targetdb.Category.label.alias("category"),
                targetdb.Carton.program,
            )
            .join(targetdb.Assignment)
            .join(targetdb.CartonToTarget)
            .join(targetdb.Target)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Carton)
            .join(targetdb.Category)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Cadence)
            .switch(targetdb.CartonToTarget)
            .join(targetdb.Magnitude)
            .switch(targetdb.Assignment)
            .join(targetdb.Hole)
            .switch(targetdb.Assignment)
            .join(targetdb.Instrument)
            .where(targetdb.Design.design_id == self.design_id)
            .dicts()
        )

        return {data["holeid"]: data for data in target_data}

    @classmethod
    async def create_async(cls, design_id: int):
        """Returns a design while creating the configuration in an executor."""

        self = cls(design_id, load_configuration=False)

        configuration = await run_in_executor(Configuration, self)
        self.configuration = configuration

        return self

    def __repr__(self):
        return f"<Design (design_id={self.design_id})>"


class BaseConfiguration:
    """A base configuration class."""

    assignment_data: ManualAssignmentData | TargetAssignmentData

    def __init__(self):

        # Configuration ID is None until we insert in the database.
        # Once set, it cannot be changed.
        self.configuration_id: int | None = None
        self.design = None
        self.design_id = None

        self.fps = FPS.get_instance()

        self._robotID = []
        self.metWokXYZ = {}

        self.robot_grid = self._initialise_grid()

    def _initialise_grid(self):

        self.robot_grid = get_robot_grid()

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

    def get_target_coords(self) -> pandas.DataFrame:
        """Returns a Pandas data frame that can be passed to the FVC routines."""

        raise NotImplementedError("This method needs to be overridden by a subclass.")

    def get_trajectory(self, simple_decollision=False):
        """Returns a trajectory dictionary from the folded position."""

        # TODO: this needs more checks and warnings when a positioner doesn't
        # get valid coordinates or when robots are disabled.

        # Just to be sure, reinitialise the grid.
        self.robot_grid = self._initialise_grid()

        a_data = self.assignment_data
        alpha0, beta0 = config["kaiju"]["lattice_position"]

        for robot in self.robot_grid.robotDict.values():
            robot.setAlphaBeta(alpha0, beta0)
            if robot.id in a_data.positioner_ids:
                index = a_data.positioner_to_index[robot.id]
                if index in a_data.valid:
                    p_coords = a_data.positioner[index]
                    robot.setAlphaBeta(p_coords[0], p_coords[1])
                    continue
            raise JaegerError(f"Positioner {robot.id} was not assigned.")

        for r in self.robot_grid.robotDict.values():
            self._robotID.append(r.id)
            self.metWokXYZ[r.id] = r.metWokXYZ

        decollide_grid(self.robot_grid, simple=simple_decollision)
        self.robot_grid.pathGenGreedy()

        if self.robot_grid.didFail:
            raise TrajectoryError(
                "Failed generating a valid trajectory. "
                "This usually means a deadlock was found."
            )

        speed = config["positioner"]["motor_speed"] / config["positioner"]["gear_ratio"]
        forward = self.robot_grid.getPathPair(speed=speed)[0]

        return forward


class Configuration(BaseConfiguration):
    """A configuration based on a target design."""

    assignment_data: TargetAssignmentData

    def __init__(self, design: Design, **kwargs):

        super().__init__(**kwargs)

        self.design = design
        self.design_id = design.design_id
        self.assignment_data = TargetAssignmentData(self)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id} "
            f"design_id={self.design_id})>"
        )

    def recompute_coordinates(self, jd: Optional[float] = None):
        """Recalculates the coordinates. ``jd=None`` uses the current time."""

        if isinstance(self.assignment_data, ManualAssignmentData):
            return

        if self.configuration_id is not None:
            raise JaegerError(
                "Cannot recompute coordinates once the configuration "
                "has been loaded to the database."
            )

        self.assignment_data.compute_coordinates(jd=jd)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd

    def get_target_coords(self) -> pandas.DataFrame:
        """Returns a Pandas data frame that can be passed to the FVC routines."""

        return pandas.DataFrame(
            {
                "robotID": self.assignment_data.positioner_ids,
                "xWokMetExpect": [],
                "yWokMetExpect": [],
                "xWokApExpect": [],
                "yWokApExpect": [],
                "xWokBossExpect": [],
                "yWokBossExpect": [],
            }
        )

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

        if isinstance(self.assignment_data, ManualAssignmentData):
            raise JaegerError("Manual configurations cannot be loaded to the database.")

        assert isinstance(self.design, Design)

        if self.configuration_id is None:

            with opsdb.database.atomic():
                configuration = opsdb.Configuration(
                    configuration_id=self.configuration_id,
                    design_id=self.design_id,
                    epoch=self.epoch,
                    calibration_version=fps_calibs_version,
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

                return self.write_to_database(replace=False)

        a_data = self.assignment_data

        focals = []
        for holeid, target in self.design.target_data.items():
            try:
                data_index = a_data.holeids.index(holeid)
                if data_index not in a_data.valid:
                    raise ValueError()

                positioner_id = a_data.positioner_ids[data_index]
                positioner_to_index = a_data.positioner_to_index[positioner_id]

                xfocal, yfocal, _ = a_data.focal[positioner_to_index, :]
                if numpy.isnan(xfocal) or numpy.isnan(yfocal):
                    xfocal = yfocal = None

            except ValueError:
                positioner_id = None
                xfocal = yfocal = None

            focals.append(
                dict(
                    assignment_pk=target["assignment_pk"],
                    xfocal=xfocal,
                    yfocal=yfocal,
                    positioner_id=positioner_id,
                    configuration_id=self.configuration_id,
                )
            )

        with opsdb.database.atomic():
            opsdb.AssignmentToFocal.insert_many(focals).execute(opsdb.database)

    def write_summary(self, overwrite=False):
        """Writes the confSummary file."""

        # TODO: some time may be saved by doing a single DB query and retrieving
        # all the info at once for all the assignments. Need to be careful
        # to maintain the order.

        if self.configuration_id is None:
            raise JaegerError("Configuration needs to be set and loaded to the DB.")

        a_data = self.assignment_data

        time = Time.now()
        rs_run = self.design.field.version.plan

        header = dict(
            configuration_id=self.configuration_id,
            targeting_version=-999,
            robostrategy_run=rs_run,
            fps_calibrations_version=fps_calibs_version,
            design_id=self.design.design_id,
            field_id=self.design.field.field_id,
            instruments="BOSS APOGEE",
            epoch=self.epoch,
            obstime=time.strftime("%a %b %d %H:%M:%S %Y"),
            MJD=int(time.mjd),  # TODO: this should be SJD
            observatory=self.design.field.observatory.label,
            temperature=-999,  # TODO
            raCen=self.design.field.racen,
            decCen=self.design.field.deccen,
        )

        fibermap, default = get_fibermap_table()

        positioners = positionerTable.set_index("positionerID")
        wok = wokCoords.set_index("holeID")

        for pid in positioners.index.tolist():

            holeID = positioners.loc[pid].holeID
            holeType = wok.loc[holeID].holeType.upper()

            for fibre in ["APOGEE", "BOSS"]:

                # Only add a row if the hole has a connected fibre of the current
                # type, even if it's not assigned.
                if fibre not in holeType:
                    continue

                # Start with the default row.
                row = default.copy()

                index = a_data.positioner_to_index.get(pid, False)
                is_fibre_ok = True
                if index is not False:
                    is_fibre_ok = a_data.fibre_types[index] == fibre

                if index is False or is_fibre_ok is False:
                    # Either the positioner was not assigned or the fibre
                    # is not the targetted one. Add an empty line.
                    row.update(
                        {
                            "positionerId": pid,
                            "holeId": holeID,
                            "fiberType": fibre,
                            "assigned": 0,
                            "valid": 0,
                            "spectrographId": 0 if fibre == "APOGEE" else 1,
                        }
                    )

                else:
                    valid = index in a_data.valid
                    if valid is True:
                        xFocal, yFocal = a_data.focal[index, 0:2]
                        alpha, beta = a_data.positioner[index, 0:2]
                    else:
                        xFocal = yFocal = alpha = beta = -999.0

                    target = a_data.design.target_data[holeID]

                    row.update(
                        {
                            "positionerId": pid,
                            "holeId": holeID,
                            "fiberType": fibre,
                            "assigned": 1,
                            "valid": int(valid),
                            "xFocal": xFocal,
                            "yFocal": yFocal,
                            "alpha": alpha,
                            "beta": beta,
                            "racat": target["ra"],
                            "deccat": target["dec"],
                            "pmra": target["pmra"] or -999.0,
                            "pmdec": target["pmdec"] or -999.0,
                            "parallax": target["parallax"] or -999.0,
                            "coord_epoch": target["epoch"] or -999.0,
                            "ra": a_data.icrs[index, 0],
                            "dec": a_data.icrs[index, 1],
                            "lambda_eff": target["lambda_eff"] or -999.0,
                            "spectrographId": 0 if fibre == "APOGEE" else 1,
                            "catalogid": target["catalogid"],
                            "carton_to_target_pk": target["carton_to_target_pk"],
                            "cadence": target["cadence"],
                            "firstcarton": target["carton"],
                            "program": target["program"],
                            "category": target["category"],
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

                fibermap.add_row(row)

        fibermap.sort(["positionerId", "fiberType"])

        if "SDSSCORE_DIR" not in os.environ:
            raise JaegerError("$SDSSCORE_DIR is not set. Cannot write summary file.")

        sdsscore_dir = os.environ["SDSSCORE_DIR"]
        path = os.path.join(
            sdsscore_dir,
            a_data.observatory.lower(),
            "summary_files",
            f"{int(self.configuration_id / 100):04d}XX",
            f"confSummary-{self.configuration_id}.par",
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


class ManualConfiguration(BaseConfiguration):
    """A configuration create manually."""

    assignment_data: ManualAssignmentData

    def __init__(
        self,
        data: pandas.DataFrame | dict,
        design_id: int = -999,
        site: str | None = None,
    ):

        super().__init__()

        self.design = None
        self.design_id = design_id
        self.epoch = None

        if site is None:
            if config["observatory"] != "${OBSERATORY}":
                site = config["observatory"]
                assert isinstance(site, str)
            else:
                raise ValueError("Unknown site.")

        self.assignment_data = ManualAssignmentData(data, site=site)

    def get_target_coords(self) -> pandas.DataFrame:
        """Returns a Pandas data frame that can be passed to the FVC routines."""

        positioner_table = positionerTable.set_index("positionerID")

        robotID = self.assignment_data.positioner_ids
        holeID = positioner_table.loc[robotID].holeID
        offline = [self.robot_grid.robotDict[pid].isOffline for pid in robotID]

        return pandas.DataFrame(
            {
                "robotID": robotID,
                "holeID": holeID,
                "xWokMetExpect": [self.metWokXYZ[rid][0] for rid in robotID],
                "yWokMetExpect": [self.metWokXYZ[rid][1] for rid in robotID],
                # "xWokApExpect": self.assignment_data.wok_apogee[:, 0],
                # "yWokApExpect": self.assignment_data.wok_apogee[:, 1],
                # "xWokBossExpect": self.assignment_data.wok_boss[:, 0],
                # "yWokBossExpect": self.assignment_data.wok_boss[:, 1],
                "offline": offline,
            }
        )

    @classmethod
    def create_random(
        cls,
        seed: int | None = None,
        safe=True,
        uniform: tuple[float, ...] | None = None,
        **kwargs,
    ):
        """Creates a random configuration using Kaiju."""

        seed = seed or numpy.random.randint(0, 1000000)
        numpy.random.seed(seed)

        robot_grid = get_robot_grid(seed=seed)

        alphaL, betaL = config["kaiju"]["lattice_position"]

        positionerIDs = []
        alphas = []
        betas = []

        # We use Kaiju for convenience in the non-safe mode.
        for robot in robot_grid.robotDict.values():
            positionerIDs.append(robot.id)

            if robot.isOffline:
                alphas.append(robot.alpha)
                betas.append(robot.beta)
                continue

            if uniform is not None:
                alpha0, alpha1, beta0, beta1 = uniform
                alphas.append(numpy.random.uniform(alpha0, alpha1))
                betas.append(numpy.random.uniform(beta0, beta1))

            else:
                if safe:
                    safe_mode = config["safe_mode"]
                    if safe_mode is False:
                        safe_mode = {"min_beta": 165, "max_beta": 195}

                    alphas.append(numpy.random.uniform(0, 359.9))
                    betas.append(
                        numpy.random.uniform(
                            safe_mode["min_beta"],
                            safe_mode["max_beta"],
                        )
                    )

                else:
                    robot.setDestinationAlphaBeta(alphaL, betaL)
                    robot.setXYUniform()
                    alphas.append(robot.alpha)
                    betas.append(robot.beta)

        # Build an assignment dictionary.
        data = {
            "positionerID": positionerIDs,
            "positioner_alpha": alphas,
            "positioner_beta": betas,
        }

        return cls(data, **kwargs)

    @classmethod
    def create_folded(cls, **kwargs):
        """Creates a folded configuration."""

        npositioner = len(positionerTable["positionerID"])
        alphaL, betaL = config["kaiju"]["lattice_position"]
        data = {
            "positionerID": [pid for pid in positionerTable["positionerID"]],
            "positioner_alpha": [alphaL] * npositioner,
            "positioner_beta": [betaL] * npositioner,
        }

        return cls(data, **kwargs)


class TargetAssignmentData:
    """Information about the target assignment along with coordinate transformation."""

    observed_boresight: Observed

    icrs: ICRS
    observed: Observed
    focal: FocalPlane
    wok: Wok
    tangent: numpy.ndarray
    positioner: numpy.ndarray

    _tangent: list[Tangent]
    _positioner: list[PositionerType]

    valid: numpy.ndarray
    positioner_to_index: dict[int, int]

    def __init__(self, configuration: Configuration):

        if not isinstance(configuration.design, Design):
            raise JaegerError("Invalid configuration design.")

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.design.design_id

        self.observatory: str = self.design.field.observatory.label.upper()
        self.site = Site(self.observatory)

        self.positioner_table = positionerTable.set_index("holeID")

        # TODO: we are limiting the wok to those holes in the list of positioners.
        # For now this is useful to work with the miniwok but it may not be what
        # we want.
        wok_table = wokCoords.set_index("holeID").loc[self.positioner_table.index]
        self.target_data = self.design.target_data

        self.holeids: list[str] = list(self.target_data.keys())

        self.positioner_ids = self.positioner_table.loc[self.holeids].positionerID
        self.positioner_ids = cast(list[int], self.positioner_ids.tolist())

        self.wok_data = wok_table.loc[self.holeids]

        assert len(self.wok_data) == len(self.holeids), "invalid number of hole_ids"

        self.fibre_types: list[str] = [
            target["fibre_type"] for target in self.target_data.values()
        ]
        self.wavelengths: list[float] = [
            INST_TO_WAVE[ft.capitalize()] for ft in self.fibre_types
        ]

        # Check that the fibre types are valid fibres for a given hole.
        if (
            not self.wok_data.reset_index()
            .apply(
                lambda row: self.fibre_types[row.name].lower() in row.holeType.lower(),
                axis=1,
            )
            .all()
        ):
            raise RuntimeError("Mismatch of fibre types to positioners.")

        self.compute_coordinates()

    def __repr__(self):
        return f"<AssignmentData (design_id={self.design_id})>"

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems."""

        target_coords = numpy.array(
            [
                [
                    target["ra"],
                    target["dec"],
                    target["pmra"],
                    target["pmdec"],
                    target["parallax"],
                ]
                for target in self.target_data.values()
            ],
            dtype=numpy.float64,
        )

        assert numpy.all(~numpy.isnan(target_coords[:, 0:2]))

        self.icrs = ICRS(
            target_coords[:, 0:2],
            pmra=numpy.nan_to_num(target_coords[:, 2], nan=0),
            pmdec=numpy.nan_to_num(target_coords[:, 3], nan=0),
            parallax=numpy.nan_to_num(target_coords[:, 4]),
        )

        self.site.set_time(jd)

        self.observed = Observed(
            self.icrs,
            wavelength=self.wavelengths,
            site=self.site,
        )

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.observed_boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        field = Field(
            self.observed,
            field_center=self.observed_boresight,
        )

        self.focal = FocalPlane(
            field,
            wavelength=self.wavelengths,
            site=self.site,
        )

        self.wok = Wok(
            self.focal,
            site=self.site,
            obsAngle=self.design.field.position_angle,
        )

        # coordio doesn't allow a single instance of Tangent or PositionerBase to
        # contain an array of holeIDs and fibre types. For now we create a list of
        # instances for each hole and fibre. We ignore warnings since those are
        # propagated to positioner_warn anyway.
        self._tangent = []
        self._positioner = []
        for ipos in range(len(self.holeids)):

            holeid = self.holeids[ipos]
            ftype = self.fibre_types[ipos]

            if ftype.upper() == "BOSS":
                Positioner = PositionerBoss
            elif ftype.upper() == "APOGEE":
                Positioner = PositionerApogee
            else:
                raise ValueError(f"Invalid fibre type {ftype}.")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                _tangent = Tangent(
                    self.wok[[ipos]],
                    holeID=holeid,
                    site=self.site,
                    wavelength=INST_TO_WAVE["GFA"],
                )
                self._tangent.append(_tangent)

                self._positioner.append(
                    Positioner(
                        _tangent,
                        site=self.site,
                        holeID=holeid,
                    )
                )

        if any([pos.positioner_warn[0] for pos in self._positioner]):
            warn(
                "Some coordinates failed while converting to "
                "positioner coordinates. Skipping."
            )

        self.valid = numpy.where([~p.positioner_warn[0] for p in self._positioner])[0]

        self.tangent = numpy.vstack(self._tangent).astype(numpy.float32)
        self.positioner = numpy.vstack(self._positioner).astype(numpy.float32)

        if not (self.positioner[self.valid][:, 1] < 180).all():
            raise JaegerError("Some beta coordinates are > 180.")

        self.positioner_to_index = {pid: i for i, pid in enumerate(self.positioner_ids)}


class ManualAssignmentData:
    """A manual assignment of robots to robot positions.

    Parameters
    ----------
    data
        Either a Pandas data frame or a dictionary that must at least contain the
        columns ``holeID``, ``positionerID``, ``positioner_alpha``, and
        ``positioner_beta``.
    site
        The observatory.

    """

    def __init__(self, data: pandas.DataFrame | dict, site: str = "APO"):

        if isinstance(data, dict):
            data = pandas.DataFrame(data)

        self.data = data

        self.site = Site(site)
        self.site.set_time()

        self.positioner_ids: list[int] = data["positionerID"].tolist()

        if "holeID" in data:
            self.holeids = data["holeID"].tolist()
        else:
            positioner_data = positionerTable.set_index("positionerID")
            self.holeids = positioner_data.loc[self.positioner_ids].holeID.tolist()

        self.positioner_to_index = {pid: i for i, pid in enumerate(self.positioner_ids)}
        self.valid = numpy.arange(len(self.positioner_ids))

        self.positioner = data.loc[:, ["positioner_alpha", "positioner_beta"]]
        self.positioner = self.positioner.to_numpy()

        self.wavelengths: list[float] = [INST_TO_WAVE["GFA"]] * len(self.positioner_ids)

        self.wok_apogee: numpy.ndarray
        self.wok_boss: numpy.ndarray
        self.wok_metrology: numpy.ndarray

        if "wok_x" not in data:
            # self.wok_apogee = self._to_wok("apogee")
            # self.wok_boss = self._to_wok("boss")
            self.wok_metrology = self._to_wok("metrology")

    def _to_wok(self, fibre_type: str):
        """Returns wok coordinates from positioner."""

        if fibre_type == "metrology":
            PositionerClass = PositionerMetrology
        elif fibre_type == "apogee":
            PositionerClass = PositionerApogee
        elif fibre_type == "boss":
            PositionerClass = PositionerBoss
        else:
            raise ValueError(f"Invalid fibre_type {fibre_type}.")

        wok_coords = numpy.zeros((len(self.positioner_ids), 2), dtype=numpy.float32)

        for ii, (alpha, beta) in enumerate(self.positioner):

            positioner = PositionerClass(
                [[alpha, beta]],
                site=self.site,
                holeID=self.holeids[ii],
            )

            tangent = Tangent(
                positioner,
                wavelength=self.wavelengths[ii],
                holeID=self.holeids[ii],
                site=self.site,
            )

            wok = Wok(tangent, site=self.site)

            wok_coords[ii] = wok[0][:2]

        return wok_coords
