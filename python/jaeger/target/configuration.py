#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-10
# @Filename: configuration.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os
import warnings

from typing import TYPE_CHECKING, Optional, Union

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
    PositionerMetrology,
    Site,
    Tangent,
    Wok,
)
from coordio.conv import (
    positionerToTangent,
    tangentToPositioner,
    tangentToWok,
    wokToTangent,
)
from coordio.defaults import INST_TO_WAVE, POSITIONER_HEIGHT, calibration, getHoleOrient
from sdssdb.peewee.sdss5db import opsdb

from jaeger import FPS, config
from jaeger.exceptions import JaegerError, TrajectoryError

from .tools import decollide_grid, get_robot_grid, warn


if TYPE_CHECKING:
    from .design import Design


__all__ = [
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "TargetAssignmentData",
    "ManualAssignmentData",
]

PositionerType = Union[PositionerApogee, PositionerBoss]


def get_fibermap_table() -> tuple[Table, dict]:
    """Returns a stub for the FIBERMAP table and a default entry,"""

    fiber_map_data = [
        ("positionerId", numpy.int16),
        ("holeId", "S7"),
        ("fiberType", "S10"),
        ("assigned", numpy.int16),
        ("on_target", numpy.int16),
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

        self.robot_grid = self._initialise_grid()

    def _initialise_grid(self):

        self.robot_grid = get_robot_grid()

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

    def get_trajectory(self, simple_decollision=False):
        """Returns a trajectory dictionary from the folded position."""

        assert isinstance(self, Configuration)

        # Just to be sure, reinitialise the grid.
        self.robot_grid = self._initialise_grid()

        ftable = self.assignment_data.fibre_table
        alpha0, beta0 = config["kaiju"]["lattice_position"]

        for robot in self.robot_grid.robotDict.values():
            robot.setAlphaBeta(alpha0, beta0)
            if robot.id not in ftable.index.get_level_values(0):
                raise JaegerError(f"Positioner {robot.id} is not assigned.")

            # Get the first of the three fibres since all have the same alpha, beta.
            rdata = ftable.loc[robot.id].iloc[0]
            if rdata.valid:
                robot.setAlphaBeta(rdata.alpha, rdata.beta)
                continue
            raise JaegerError(f"Positioner {robot.id} has no valid coordinates.")

        for r in self.robot_grid.robotDict.values():
            cols = ["xwok_kaiju", "ywok_kaiju", "zwok_kaiju"]
            ftable.loc[(r.id, "APOGEE"), cols] = r.apWokXYZ
            ftable.loc[(r.id, "BOSS"), cols] = r.bossWokXYZ
            ftable.loc[(r.id, "Metrology"), cols] = r.metWokXYZ

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

        if self.configuration_id is None:

            with opsdb.database.atomic():
                configuration = opsdb.Configuration(
                    configuration_id=self.configuration_id,
                    design_id=self.design_id,
                    epoch=self.epoch,
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
        for data in a_data.itertuples():
            pid = data.Index[0]
            hole_id = data.hole_id
            try:
                if data.valid == 0:
                    raise ValueError(f"Invalid coordinate found for positioner {pid}.")

                xfocal, yfocal = data.xfocal, data.yfocal
                if xfocal == -999.0 or yfocal == -999.0:
                    xfocal = yfocal = None

            except ValueError:
                xfocal = yfocal = None

            if hole_id in self.design.target_data:
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

    def write_summary(self, overwrite=False):
        """Writes the confSummary file."""

        # TODO: some time may be saved by doing a single DB query and retrieving
        # all the info at once for all the assignments. Need to be careful
        # to maintain the order.

        if self.configuration_id is None:
            raise JaegerError("Configuration needs to be set and loaded to the DB.")

        fdata = self.assignment_data.fibre_table

        time = Time.now()
        rs_run = self.design.field.version.plan

        header = {
            "configuration_id": self.configuration_id,
            "targeting_version": -999,
            "robostrategy_run": rs_run,
            "fps_calibrations_version": calibration.fps_calibs_version,
            "design_id": self.design.design_id,
            "field_id": self.design.field.field_id,
            "instruments": "BOSS APOGEE",
            "epoch": self.epoch,
            "obstime": time.strftime("%a %b %d %H:%M:%S %Y"),
            "MJD": int(time.mjd),  # TODO: this should be SJD
            "observatory": self.design.field.observatory.label,
            "temperature": -999,  # TODO
            "raCen": self.design.field.racen,
            "decCen": self.design.field.deccen,
        }

        fibermap, default = get_fibermap_table()

        for row_data in fdata.itertuples():

            pid, fibre_type = row_data.Index
            hole_id = row_data.hole_id

            # Start with the default row.
            row = default.copy()

            if fibre_type.upper() == "APOGEE":
                spec_id = 0
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
                    "xFocal": row_data.xfocal,
                    "yFocal": row_data.yfocal,
                    "alpha": row_data.alpha,
                    "beta": row_data.beta,
                    "ra": row_data.ra_epoch,
                    "dec": row_data.ra_epoch,
                    "spectrographId": spec_id,
                }
            )

            # And now only the one that is associated with a target.
            if row_data.assigned == 1 and hole_id in self.design.target_data:
                target = self.design.target_data[hole_id]
                row.update(
                    {
                        "racat": target["ra"],
                        "deccat": target["dec"],
                        "pmra": target["pmra"] or -999.0,
                        "pmdec": target["pmdec"] or -999.0,
                        "parallax": target["parallax"] or -999.0,
                        "coord_epoch": target["epoch"] or -999.0,
                        "lambda_eff": target["lambda_eff"] or -999.0,
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
            self.assignment_data.observatory.lower(),
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

        print(fibermap)
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

        assert calibration.positionerTable is not None, "FPS calibrations not loaded."

        positionerTable = calibration.positionerTable.set_index("positionerID")
        npositioner = len(positionerTable.loc["positionerID"])
        alphaL, betaL = config["kaiju"]["lattice_position"]
        data = {
            "positionerID": [pid for pid in positionerTable.loc["positionerID"]],
            "positioner_alpha": [alphaL] * npositioner,
            "positioner_beta": [betaL] * npositioner,
        }

        return cls(data, **kwargs)


class TargetAssignmentData:
    """Information about the target assignment along with coordinate transformation."""

    boresight: Observed

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
        ("collided", numpy.int8, 0),
        ("wavelength", numpy.float32, -999.0),
        ("ra_icrs", numpy.float64, -999.0),
        ("dec_icrs", numpy.float64, -999.0),
        ("ra_epoch", numpy.float64, -999.0),
        ("dec_epoch", numpy.float64, -999.0),
        ("alt", numpy.float64, -999.0),
        ("az", numpy.float64, -999.0),
        ("xfocal", numpy.float64, -999.0),
        ("yfocal", numpy.float64, -999.0),
        ("xwok", numpy.float64, -999.0),
        ("ywok", numpy.float64, -999.0),
        ("zwok", numpy.float64, -999.0),
        ("xwok_kaiju", numpy.float64, -999.0),
        ("ywok_kaiju", numpy.float64, -999.0),
        ("zwok_kaiju", numpy.float64, -999.0),
        ("xtangent", numpy.float64, -999.0),
        ("ytangent", numpy.float64, -999.0),
        ("ztangent", numpy.float64, -999.0),
        ("alpha", numpy.float64, -999.0),
        ("beta", numpy.float64, -999.0),
    ]

    def __init__(self, configuration: Configuration):

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.design.design_id

        self.observatory: str = self.design.field.observatory.label.upper()
        self.site = Site(self.observatory)

        assert (
            calibration.positionerTable is not None
            and calibration.wokCoords is not None
        ), "FPS calibrations not loaded."

        self.wok_data = pandas.merge(
            calibration.positionerTable.reset_index(),
            calibration.wokCoords.reset_index(),
            on="holeID",
        )
        self.wok_data.set_index("positionerID", inplace=True)

        self.target_data = self.design.target_data

        names, _, values = zip(*self._columns)
        self._defaults = {
            name: values[i] for i, name in enumerate(names) if values[i] is not None
        }

        self.fibre_table: pandas.DataFrame
        self.compute_coordinates()

    def __repr__(self):
        return f"<AssignmentData (design_id={self.design_id})>"

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems."""

        kaiju_config = config["kaiju"]
        alpha0, beta0 = kaiju_config["lattice_position"]

        self.site.set_time(jd)
        self._create_fibre_table()

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        data = {}
        for pid in self.wok_data.index:

            positioner_data = self.wok_data.loc[pid]
            hole_id = positioner_data.holeID

            target_fibre_type: str | None = None
            if hole_id in self.target_data:

                # First do the assigned fibre.
                ftype = self.target_data[hole_id]["fibre_type"].upper()
                target_fibre_type = ftype

                positioner_data = self.icrs_to_positioner(
                    pid,
                    ftype,
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
                    update=False,
                )
                data[(pid, ftype)] = icrs_data

        # Now do a single update of the whole fibre table.
        self.fibre_table.update(pandas.DataFrame.from_dict(data, orient="index"))

        # Final validation
        self.validate()

    def _create_fibre_table(self):
        """Creates an empty fibre table."""

        names, dtypes, _ = zip(*self._columns)

        # Create empty dataframe with zero values. Fill out all the index data.
        npositioner = len(self.wok_data)
        base = numpy.zeros((npositioner * 3,), dtype=list(zip(names, dtypes)))

        i = 0
        for pid in self.wok_data.index.tolist():
            for ft in ["APOGEE", "BOSS", "Metrology"]:
                base["positioner_id"][i] = pid
                base["fibre_type"][i] = ft
                base["hole_id"] = self.wok_data.loc[pid].holeID
                i += 1

        self.fibre_table = pandas.DataFrame(base)

        self.fibre_table.fibre_type = self.fibre_table.fibre_type.astype("category")
        self.fibre_table.hole_id = self.fibre_table.hole_id.astype("string")

        self.fibre_table.set_index(["positioner_id", "fibre_type"], inplace=True)
        self.fibre_table = self.fibre_table.sort_index()

    def validate(self):
        """Validates the fibre table."""

        alpha_beta = self.fibre_table[["alpha", "beta"]]
        na = alpha_beta.isna().any(axis=1)
        over_180 = self.fibre_table.beta > 180

        self.fibre_table.loc[na | over_180, "valid"] = 0

    def icrs_to_positioner(
        self,
        positioner_id: int,
        fibre_type: str,
        update: bool = True,
        **kwargs,
    ):
        """Converts from ICRS coordinates."""

        hole_id = self.wok_data.loc[positioner_id].holeID
        wavelength = INST_TO_WAVE.get(fibre_type.capitalize(), INST_TO_WAVE["GFA"])

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            ra = self.target_data[hole_id]["ra"]
            dec = self.target_data[hole_id]["dec"]
            pmra = self.target_data[hole_id]["pmra"]
            pmdec = self.target_data[hole_id]["pmdec"]
            parallax = self.target_data[hole_id]["parallax"]
            position_angle = self.design.field.position_angle
            epoch = self.target_data[hole_id]["epoch"]

            icrs = ICRS(
                [[ra, dec]],
                pmra=numpy.nan_to_num(pmra, nan=0),
                pmdec=numpy.nan_to_num(pmdec, nan=0),
                parallax=numpy.nan_to_num(parallax),
                epoch=epoch,
            )

            assert self.site.time
            icrs_epoch = icrs.to_epoch(self.site.time.jd, site=self.site)

            observed = Observed(icrs, wavelength=wavelength, site=self.site)
            field = Field(observed, field_center=self.boresight)
            focal = FocalPlane(field, wavelength=wavelength, site=self.site)
            wok = Wok(focal, site=self.site, obsAngle=position_angle)

            positioner_data = self.wok_data.loc[positioner_id]
            hole_orient = getHoleOrient(self.site.name, hole_id)

            if fibre_type == "APOGEE":
                xBeta = positioner_data.apX
                yBeta = positioner_data.apY
            elif fibre_type == "BOSS":
                xBeta = positioner_data.bossX
                yBeta = positioner_data.bossY
            elif fibre_type == "Metrology":
                xBeta = positioner_data.metX
                yBeta = positioner_data.metY
            else:
                raise ValueError(f"Invalid fibre type {fibre_type}.")

            tangent = wokToTangent(
                wok[0, 0],
                wok[0, 1],
                POSITIONER_HEIGHT,
                *hole_orient,
                dx=positioner_data.dx,
                dy=positioner_data.dy,
            )

            alpha, beta, _ = tangentToPositioner(
                tangent[0][0],
                tangent[1][0],
                xBeta,
                yBeta,
                la=positioner_data.alphaArmLen,
                alphaOffDeg=positioner_data.alphaOffset,
                betaOffDeg=positioner_data.betaOffset,
            )

        row = self._defaults.copy()
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
                "xtangent": tangent[0][0],
                "ytangent": tangent[1][0],
                "ztangent": tangent[2][0],
                "alpha": alpha,
                "beta": beta,
            }
        )
        row.update(kwargs)

        if update:
            self.fibre_table.loc[(positioner_id, fibre_type)] = pandas.Series(row)

        return row

    def positioner_to_icrs(
        self,
        positioner_id: int,
        fibre_type: str,
        alpha: float,
        beta: float,
        update=True,
        **kwargs,
    ):
        """Converts from positioner to ICRS coordinates."""

        wavelength = INST_TO_WAVE.get(fibre_type.capitalize(), INST_TO_WAVE["GFA"])

        assert self.site.time

        positioner_data = self.wok_data.loc[positioner_id]
        hole_id = positioner_data.holeID

        b = positioner_data[["xWok", "yWok", "zWok"]]
        iHat = positioner_data[["ix", "iy", "iz"]]
        jHat = positioner_data[["jx", "jy", "jz"]]
        kHat = positioner_data[["kx", "ky", "kz"]]

        if fibre_type == "APOGEE":
            xBeta = positioner_data.apX
            yBeta = positioner_data.apY
        elif fibre_type == "BOSS":
            xBeta = positioner_data.bossX
            yBeta = positioner_data.bossY
        elif fibre_type == "Metrology":
            xBeta = positioner_data.metX
            yBeta = positioner_data.metY
        else:
            raise ValueError(f"Invlid fibre type {fibre_type}.")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            tangent = positionerToTangent(
                alpha,
                beta,
                xBeta,
                yBeta,
                la=positioner_data.alphaArmLen,
                alphaOffDeg=positioner_data.alphaOffset,
                betaOffDeg=positioner_data.betaOffset,
            )

            wok = tangentToWok(
                tangent[0],
                tangent[1],
                POSITIONER_HEIGHT,
                b,
                iHat,
                jHat,
                kHat,
                dx=positioner_data.dx,
                dy=positioner_data.dy,
            )

            focal = FocalPlane(
                Wok([wok], site=self.site, obsAngle=self.design.field.position_angle),
                wavelength=wavelength,
                site=self.site,
            )

            field = Field(focal, field_center=self.boresight)
            obs = Observed(field, site=self.site, wavelength=wavelength)
            icrs = ICRS(obs, epoch=self.site.time.jd)

        row = self._defaults.copy()
        row.update(
            {
                "hole_id": hole_id,
                "wavelength": wavelength,
                "ra_epoch": icrs[0, 0],
                "dec_epoch": icrs[0, 1],
                "xfocal": focal[0, 0],
                "yfocal": focal[0, 1],
                "xwok": wok[0],
                "ywok": wok[1],
                "zwok": wok[2],
                "alpha": alpha,
                "beta": beta,
            }
        )
        row.update(kwargs)

        if update:
            self.fibre_table.loc[(positioner_id, fibre_type)] = pandas.Series(row)

        return row


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
            assert calibration.positionerTable is not None
            positioner_data = calibration.positionerTable.set_index("positionerID")
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
            self.wok_apogee = self._to_wok("apogee")
            self.wok_boss = self._to_wok("boss")
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
