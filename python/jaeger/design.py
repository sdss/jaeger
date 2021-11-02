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

from jaeger import config
from jaeger.exceptions import JaegerError, JaegerUserWarning, TrajectoryError


if TYPE_CHECKING:
    from kaiju import RobotGridCalib


__all__ = [
    "Design",
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "TargetAssignmentData",
    "ManualAssignmentData",
    "unwind_or_explode",
    "get_robot_grid",
]


PositionerType = Union[PositionerApogee, PositionerBoss]


def warn(message):
    warnings.warn(message, JaegerUserWarning)


def get_robot_grid(seed: int = 0):
    """Returns a new robot grid with the destination set to the lattice position."""

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


def unwind_or_explode(
    current_positions: dict[int, tuple[float, float]],
    only_connected: bool = False,
    explode=False,
    explode_deg=20.0,
    simple_decollision=False,
):
    """Folds all the robots to the lattice position."""

    robot_grid = get_robot_grid()

    for robot in robot_grid.robotDict.values():
        if robot.id not in current_positions:
            if only_connected:
                continue
            else:
                raise ValueError(f"Positioner {robot.id} is not connected.")

        robot_position = current_positions[robot.id]
        robot.setAlphaBeta(robot_position[0], robot_position[1])

    decollide_grid(robot_grid, simple=simple_decollision)

    if explode is False:
        robot_grid.pathGenGreedy()
    else:
        robot_grid.pathGenEscape(explode_deg)

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

    def __init__(self, design_id: int):

        if targetdb.database.connected is False:
            raise RuntimeError("Database is not connected.")

        if wokCoords is None:
            raise RuntimeError("Cannot retrieve wok calibration. Is $WOKCALIB_DIR set?")

        self.design_id = design_id

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field = self.design.field
        self.assignments: list[targetdb.Assignment] = list(self.design.assignments)

        self.configuration = Configuration(self)

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

        self.robot_grid = self._initialise_grid()

    def _initialise_grid(self):

        self.robot_grid = get_robot_grid()

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

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
                if index in a_data.valid_index:
                    p_coords = a_data.positioner[index]
                    robot.setAlphaBeta(p_coords[0], p_coords[1])
                    continue
            warn(f"Positioner {robot.id} was not assigned.")

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

    def __init__(self, design: Design):

        self.design = design
        self.design_id = design.design_id
        self.assignment_data = TargetAssignmentData(self)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd

        super().__init__()

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id}"
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
        for assignment in self.design.assignments:
            assignment_pk = assignment.pk
            try:
                data_index = a_data.assignments.index(assignment)
                if data_index not in a_data.valid_index:
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
                    assignment_pk=assignment_pk,
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
            observatory=a_data.observatory,
            temperature=-999,  # TODO
            raCen=self.design.field.racen,
            decCen=self.design.field.deccen,
        )

        fibermap, default = get_fibermap_table()

        positioners = positionerTable.reset_index("positionerID")
        wok = wokCoords.reset_index("holeID")

        for pid in positioners.positionerID.tolist():

            holeID = positioners.loc[pid].holeID.values[0]
            holeType = wok.loc[holeID].holeType.values[0].upper()

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
                    valid = index in a_data.valid_index
                    if valid is True:
                        xFocal, yFocal = a_data.focal[index, 0:2]
                        alpha, beta = a_data.positioner[index, 0:2]
                    else:
                        xFocal = yFocal = alpha = beta = -999.0

                    target = a_data.targets[index]
                    c2t = a_data.assignments[index].carton_to_target

                    try:
                        cadence = c2t.cadence.label
                    except peewee.DoesNotExist:
                        cadence = ""

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
                            "racat": target.ra,
                            "deccat": target.dec,
                            "pmra": target.pmra or -999.0,
                            "pmdec": target.pmdec or -999.0,
                            "parallax": target.parallax or -999.0,
                            "coord_epoch": target.epoch or -999.0,
                            "ra": a_data.icrs[index, 0],
                            "dec": a_data.icrs[index, 1],
                            "lambda_eff": c2t.lambda_eff or -999.0,
                            "spectrographId": 0 if fibre == "APOGEE" else 1,
                            "catalogid": target.catalogid,
                            "cadence": cadence,
                            "firstcarton": c2t.carton.carton,
                            "program": c2t.carton.program,
                            "category": c2t.carton.category.label,
                        }
                    )

                    mag = c2t.magnitudes
                    if len(mag) > 0:
                        mag = mag[0]
                        optical_mag = [
                            getattr(mag, m) or -999.0 for m in ["g", "r", "i", "z"]
                        ]
                        optical_mag = [-999.0] + optical_mag
                    else:
                        optical_mag = [-999.0] * 5
                        mag = None

                    if mag:
                        row.update(
                            {
                                "mag": optical_mag,
                                "optical_prov": mag.optical_prov or "",
                                "bp_mag": mag.bp or -999.0,
                                "gaia_g_mag": mag.gaia_g or -999.0,
                                "rp_mag": mag.rp or -999.0,
                                "h_mag": mag.h or -999.0,
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

    def __init__(self, data: pandas.DataFrame | dict, design_id: int = -999):

        super().__init__()

        self.design = None
        self.design_id = design_id
        self.epoch = None

        self.assignment_data = ManualAssignmentData(data)

    @classmethod
    def create_random(
        cls,
        seed: int | None = None,
        safe=True,
        uniform: tuple[float, ...] | None = None,
        design_id: int = -999,
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

            if uniform is not None:
                alpha0, alpha1, beta0, beta1 = uniform
                alphas.append(numpy.random.uniform(alpha0, alpha1))
                betas.append(numpy.random.uniform(beta0, beta1))

            else:
                if safe:
                    safe_mode = config["safe_mode"]
                    if safe_mode is False:
                        safe_mode = {"min_beta": 160, "max_beta": 220}

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

        return cls(data, design_id=design_id)

    @classmethod
    def create_folded(cls, design_id: int = -999):
        """Creates a folded configuration."""

        npositioner = len(positionerTable["positionerID"])
        alphaL, betaL = config["kaiju"]["lattice_position"]
        data = {
            "positionerID": [pid for pid in positionerTable["positionerID"]],
            "positioner_alpha": [alphaL] * npositioner,
            "positioner_beta": [betaL] * npositioner,
        }

        return cls(data, design_id=design_id)


class TargetAssignmentData:
    """Information about the target assignment along with coordinate transformation."""

    observed_boresight: Observed

    icrs: ICRS
    observed: Observed
    focal: FocalPlane
    tangent: Tangent
    positioner: numpy.ndarray

    positioner_objs: list[PositionerType]
    valid_index: numpy.ndarray
    positioner_to_index: dict[int, int]

    def __init__(self, configuration: Configuration):

        if not isinstance(configuration.design, Design):
            raise JaegerError("Invalid configuration design.")

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.design.design_id

        self.observatory: str = self.design.field.observatory.label.upper()
        self.site = Site(self.observatory)

        positioner_table = positionerTable.set_index("holeID")

        # TODO: we are limiting the wok to those holes in the list of positioners.
        # For now this is useful to work with the miniwok but it may not be what
        # we want.
        wok_table = wokCoords.set_index("holeID").loc[positioner_table.index]
        self.assignments = [
            assg
            for assg in self.design.assignments
            if assg.hole.holeid in wok_table.index
        ]

        self.holeids: list[str] = [assg.hole.holeid for assg in self.assignments]

        self.positioner_ids = positioner_table.loc[self.holeids].positionerID.tolist()
        self.positioner_ids = cast(list[int], self.positioner_ids)

        self.targets: list[targetdb.Target] = [
            assignment.carton_to_target.target for assignment in self.assignments
        ]

        self.wok_data = wok_table.loc[self.holeids]
        assert isinstance(self.wok_data, pandas.DataFrame)

        assert len(self.wok_data) == len(self.holeids), "invalid number of hole_ids"

        self.fibre_types: list[str] = [
            assg.instrument.label for assg in self.assignments
        ]
        self.wavelengths: list[float] = [
            INST_TO_WAVE[ft.capitalize()] for ft in self.fibre_types
        ]

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

        target_data = (
            targetdb.Target.select(
                targetdb.Target.ra,
                targetdb.Target.dec,
                targetdb.Target.pmra,
                targetdb.Target.pmdec,
                targetdb.Target.parallax,
            )
            .join(targetdb.CartonToTarget)
            .join(targetdb.Assignment)
            .join(targetdb.Design)
            .switch(targetdb.Assignment)
            .join(targetdb.Hole)
            .where(
                targetdb.Design.design_id == self.design_id,
                targetdb.Hole.holeid.in_(self.holeids),
            )
            .tuples()
        )
        target_data = numpy.array(target_data, dtype=numpy.float64)

        assert numpy.all(~numpy.isnan(target_data[:, 0:2]))

        self.icrs = ICRS(
            target_data[:, 0:2],
            pmra=numpy.nan_to_num(target_data[:, 2], nan=0),
            pmdec=numpy.nan_to_num(target_data[:, 3], nan=0),
            parallax=numpy.nan_to_num(target_data[:, 4]),
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

        wok = Wok(
            self.focal,
            site=self.site,
            obsAngle=self.design.field.position_angle,
        )

        self.tangent = Tangent(
            wok,
            holeID=self.holeids,
            site=self.site,
            wavelength=self.wavelengths,  # This just to prevent a warning.
        )

        # coordio doesn't allow a single instance of PositionerBase to contain
        # an array of holeIDs and fibre types. For now we create a list of positioner
        # instances for each hole and fibre. We ignore warnings since those are
        # propagated to positioner_warn anyway.
        self.positioner_objs = []
        for ipos in range(len(self.targets)):
            tan_coords = self.tangent[[ipos]]

            ftype = self.fibre_types[ipos]
            holeid = self.holeids[ipos]

            if ftype.upper() == "BOSS":
                positioner_class = PositionerBoss
            elif ftype.upper() == "APOGEE":
                positioner_class = PositionerApogee
            else:
                raise ValueError(f"Invalid fibre type {ftype}.")

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")

                self.positioner_objs.append(
                    positioner_class(
                        tan_coords,
                        site=self.site,
                        holeID=holeid,
                    )
                )

        if any([pos.positioner_warn[0] for pos in self.positioner_objs]):
            warn(
                "Some coordinates failed while converting to "
                "positioner coordinates. Skipping."
            )

        self.valid_index = numpy.where(
            [~p.positioner_warn[0] for p in self.positioner_objs]
        )[0]

        self.positioner = numpy.vstack(
            [p.astype(numpy.float32) for p in self.positioner_objs]
        )

        if not (self.positioner[self.valid_index][:, 1] < 180).all():
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

    """

    def __init__(self, data: pandas.DataFrame | dict):

        if isinstance(data, dict):
            data = pandas.DataFrame(data)

        self.data = data

        self.positioner_ids: list[int] = data["positionerID"].tolist()

        if "holeID" in data:
            self.holeids = data["holeID"].tolist()
        else:
            self.holeids = None

        self.positioner_to_index = {pid: i for i, pid in enumerate(self.positioner_ids)}
        self.valid_index = numpy.arange(len(self.positioner_ids))

        self.positioner = data.loc[:, ["positioner_alpha", "positioner_beta"]]
        self.positioner = self.positioner.to_numpy()

        self.wok_metrology: numpy.ndarray

        if "wok_x" not in data:
            self.wok_metrology = self._to_wok("metrology")

    def _to_wok(self, fibre_type: str):
        """Returns wok coordinates from positioner."""

        wok_coords = numpy.zeros((len(self.positioner_ids), 2), dtype=numpy.float32)
        for ii, (alpha, beta) in enumerate(self.positioner):
            if fibre_type == "metrology":
                positioner = PositionerMetrology([[alpha, beta]])
            wok = Wok(positioner)
            wok_coords[ii] = wok[0]

        return wok_coords
