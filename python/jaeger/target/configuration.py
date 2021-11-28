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
from coordio.defaults import INST_TO_WAVE, POSITIONER_HEIGHT, calibration
from sdssdb.peewee.sdss5db import opsdb, targetdb

from jaeger import FPS, config
from jaeger.exceptions import JaegerError, TrajectoryError
from jaeger.kaiju import (
    decollide_in_executor,
    get_path_pair_in_executor,
    get_robot_grid,
    warn,
)

from .tools import positioner_to_wok, wok_to_positioner


if TYPE_CHECKING:
    from .design import Design


__all__ = [
    "BaseConfiguration",
    "Configuration",
    "ManualConfiguration",
    "AssignmentData",
]

PositionerType = Union[PositionerApogee, PositionerBoss]


def get_fibermap_table(length: int) -> tuple[numpy.ndarray, dict]:
    """Returns a stub for the FIBERMAP table and a default entry,"""

    fiber_map_data = [
        ("positionerId", numpy.int16),
        ("holeId", "U7"),
        ("fiberType", "U10"),
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
        ("optical_prov", "U10"),
        ("bp_mag", numpy.float32),
        ("gaia_g_mag", numpy.float32),
        ("rp_mag", numpy.float32),
        ("h_mag", numpy.float32),
        ("catalogid", numpy.int64),
        ("carton_to_target_pk", numpy.int64),
        ("cadence", "U20"),
        ("firstcarton", "U25"),
        ("program", "U20"),
        ("category", "U20"),
        ("sdssv_boss_target0", numpy.int64),
        ("sdssv_apogee_target0", numpy.int64),
    ]

    names, formats = zip(*fiber_map_data)

    fibermap = numpy.empty((length,), dtype={"names": names, "formats": formats})

    # Define a default row with all set to "" or -999. depending on column data type.
    default = {}
    for i in range(len(names)):
        name = names[i]
        dd = numpy.dtype(formats[i])
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

    assignment_data: BaseAssignmentData

    def __init__(self):

        if len(calibration.positionerTable) == 0:
            raise ValueError("FPS calibrations not loaded or the array is empty.")

        # Configuration ID is None until we insert in the database.
        # Once set, it cannot be changed.
        self.configuration_id: int | None = None

        self.design: Design | None = None
        self.design_id: int | None = None

        self.fps = FPS.get_instance()

        self.robot_grid = self._initialise_grid()

    def _initialise_grid(self):

        self.robot_grid = get_robot_grid()

        return self.robot_grid

    def __repr__(self):
        return f"<Configuration (configuration_id={self.configuration_id}>"

    async def get_trajectory(
        self,
        decollide: bool = False,
        simple_decollision: bool = False,
    ):
        """Returns a trajectory dictionary from the folded position."""

        assert isinstance(self, BaseConfiguration)

        # Just to be sure, reinitialise the grid.
        self.robot_grid = self._initialise_grid()

        ftable = self.assignment_data.fibre_table
        alpha0, beta0 = config["kaiju"]["lattice_position"]

        for robot in self.robot_grid.robotDict.values():
            if robot.id not in ftable.index.get_level_values(0):
                raise JaegerError(f"Positioner {robot.id} is not assigned.")

            # Get the first of the three fibres since all have the same alpha, beta.
            rdata = ftable.loc[robot.id].iloc[0]
            if rdata.valid:
                robot.setAlphaBeta(rdata.alpha, rdata.beta)
                robot.setDestinationAlphaBeta(alpha0, beta0)
                continue
            raise JaegerError(f"Positioner {robot.id} has no valid coordinates.")

        for r in self.robot_grid.robotDict.values():
            cols = ["xwok_kaiju", "ywok_kaiju", "zwok_kaiju"]
            ftable.loc[(r.id, "APOGEE"), cols] = r.apWokXYZ
            ftable.loc[(r.id, "BOSS"), cols] = r.bossWokXYZ
            ftable.loc[(r.id, "Metrology"), cols] = r.metWokXYZ

        if decollide:
            # TODO: if this is run, the configuration will change and we don't know
            # where positioners are placed.
            await decollide_in_executor(self.robot_grid, simple=simple_decollision)

        result = await get_path_pair_in_executor(self.robot_grid)
        _, from_destination, did_fail, deadlocks = result
        if did_fail:
            raise TrajectoryError(
                "Failed generating a valid trajectory. "
                f"{len(deadlocks)} deadlocks were found."
            )

        return from_destination

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
                    configuration_id=self.configuration_id,
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

    def write_summary(self, overwrite=False):
        """Writes the confSummary file."""

        # TODO: some time may be saved by doing a single DB query and retrieving
        # all the info at once for all the assignments. Need to be careful
        # to maintain the order.

        if self.configuration_id is None:
            raise JaegerError("Configuration needs to be set and loaded to the DB.")

        adata = self.assignment_data
        fdata = self.assignment_data.fibre_table

        time = Time.now()

        design = self.design

        header = {
            "configuration_id": self.configuration_id,
            "targeting_version": -999,
            "robostrategy_run": "NA",
            "fps_calibrations_version": calibration.fps_calibs_version,
            "design_id": self.design_id,
            "field_id": -999,
            "instruments": "BOSS APOGEE",
            "epoch": adata.site.time.jd if adata.site.time else -999,
            "obstime": time.strftime("%a %b %d %H:%M:%S %Y"),
            "MJD": int(time.mjd),  # TODO: this should be SJD
            "observatory": adata.observatory,
            "temperature": -999,  # TODO
            "raCen": -999.0,
            "decCen": -999.0,
        }

        if design:
            header.update(
                {
                    "robostrategy_run": design.field["rs_run"],
                    "field_id": design.field["field_id"],
                    "raCen": design.field["racen"],
                    "decCen": design.field["deccen"],
                }
            )

        fibermap, default = get_fibermap_table(len(fdata))

        i = 0
        for index, row_data in fdata.iterrows():

            index = cast(tuple, index)

            pid, fibre_type = index
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

        write_ndarray_to_yanny(
            path,
            [fibermap],
            structnames=["FIBERMAP"],
            hdr=header,
            enums={"fiberType": ("FIBERTYPE", ("BOSS", "APOGEE", "METROLOGY", "NONE"))},
        )


class Configuration(BaseConfiguration):
    """A configuration based on a target design."""

    assignment_data: AssignmentData

    def __init__(self, design: Design, **kwargs):

        super().__init__(**kwargs)

        self.design = design
        self.design_id = design.design_id
        self.assignment_data = AssignmentData(self)

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id} "
            f"design_id={self.design_id})>"
        )

    def recompute_coordinates(self, jd: Optional[float] = None):
        """Recalculates the coordinates. ``jd=None`` uses the current time."""

        if self.configuration_id is not None:
            raise JaegerError(
                "Cannot recompute coordinates once the configuration "
                "has been loaded to the database."
            )

        self.assignment_data.compute_coordinates(jd=jd)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd


class ManualConfiguration(BaseConfiguration):
    """A configuration create manually.

    Parameters
    ----------
    target_data
        A dictionary containing the targeting information. It must be a
        mapping of hole ID to dictionary. The hole ID dictionaries must
        include one of the following pairs: ``(ra_icrs, dec_icrs)``,
        ``(xwok, ywok)``, or ``(alpha, beta)`` if order of priority.
        The remaining coordinates will be filled out using coordinate
        transformations. If ``ra_icrs/dec_icrs`` are passed, the column
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
    ):

        super().__init__()

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
        )

    @classmethod
    def create_folded(cls, **kwargs):
        """Creates a folded configuration."""

        positionerTable = calibration.positionerTable.reset_index()
        alphaL, betaL = config["kaiju"]["lattice_position"]
        data = {
            positionerTable.iloc[i].holeID: {"alpha": alphaL, "beta": betaL}
            for i in range(len(positionerTable))
        }

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
        ("xwok_measured", numpy.float64, -999.0),
        ("ywok_measured", numpy.float64, -999.0),
        ("xtangent", numpy.float64, -999.0),
        ("ytangent", numpy.float64, -999.0),
        ("ztangent", numpy.float64, -999.0),
        ("alpha", numpy.float64, -999.0),
        ("beta", numpy.float64, -999.0),
    ]

    def __init__(
        self,
        configuration: Configuration | ManualConfiguration,
        observatory: Optional[str] = None,
    ):

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.configuration.design_id

        self.observatory: str
        if observatory is None:
            if self.design is None:
                raise ValueError("Cannot determine observatory.")
            self.observatory = self.design.field["observatory"].upper()
        else:
            self.observatory = observatory

        self.site = Site(self.observatory)
        self.site.set_time()

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
            self.position_angle = self.design.field["position_angle"]
        else:
            self.target_data = {}

        names, _, values = zip(*self._columns)
        self._defaults = {
            name: values[i] for i, name in enumerate(names) if values[i] is not None
        }

        self.fibre_table: pandas.DataFrame

    def __repr__(self):
        return f"<{self.__class__.__name__} (design_id={self.design_id})>"

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

    def _from_icrs(self):
        """Loads fibre data from target data using ICRS coordinates."""

        kaiju_config = config["kaiju"]
        alpha0, beta0 = kaiju_config["lattice_position"]

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
        self.fibre_table.update(pandas.DataFrame.from_dict(data, orient="index"))

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
            dec = target_data["dec"]
            pmra = target_data["pmra"]
            pmdec = target_data["pmdec"]
            parallax = target_data["parallax"]
            epoch = target_data["epoch"]

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

            positioner, tangent = wok_to_positioner(
                hole_id,
                self.site.name,
                fibre_type,
                wok[0][0],
                wok[0][1],
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
                "xtangent": tangent[0],
                "ytangent": tangent[1],
                "ztangent": tangent[2],
                "alpha": positioner[0],
                "beta": positioner[1],
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
        position_angle: float = 0.0,
        update: bool = True,
        **kwargs,
    ):
        """Converts from positioner to ICRS coordinates."""

        wavelength = INST_TO_WAVE.get(fibre_type.capitalize(), INST_TO_WAVE["GFA"])

        assert self.site.time

        hole_id = self.wok_data.at[positioner_id, "holeID"]

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)

            wok, _ = positioner_to_wok(
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


class AssignmentData(BaseAssignmentData):
    """Assignment data from a valid design with associated target information."""

    design: Design

    def __init__(self, configuration: Configuration):

        super().__init__(configuration)

        self.compute_coordinates()

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems."""

        self.fibre_table = self._create_fibre_table()
        self.site.set_time(jd)

        icrs_bore = ICRS([[self.design.field["racen"], self.design.field["deccen"]]])
        self.boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        # Load fibre data using ICRS coordinates.
        self._from_icrs()

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
        include one of the following pairs: ``(ra_icrs, dec_icrs)``,
        ``(xwok, ywok)``, or ``(alpha, beta)`` if order of priority.
        The remaining coordinates will be filled out using coordinate
        transformations. If ``ra_icrs/dec_icrs`` are passed, an additional
        column ``epoch`` is required.
    observatory
        The observatory name. If `None`, uses the value from the configuration.
    field_centre
        A tuple or array with the boresight coordinates as current epoch
        RA/Dec. If `None`, target data must be positioner or wok coordinates.
    position_angle
        The position angle of the field.

    """

    boresight: Observed | None = None

    def __init__(
        self,
        configuration: ManualConfiguration,
        target_data: dict,
        observatory: str,
        field_centre: tuple[float, float] | numpy.ndarray | None = None,
        position_angle: float = 0.0,
    ):

        super().__init__(configuration, observatory=observatory)

        self.target_data = target_data

        if field_centre is not None:
            self.field_centre = numpy.array(field_centre)
        else:
            self.field_centre = None

        self.position_angle = position_angle

        self.compute_coordinates()

    def compute_coordinates(self, jd: Optional[float] = None):
        """Computes coordinates in different systems."""

        self.fibre_table = self._create_fibre_table()
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
            self._from_icrs()
        elif "xwok" in sample_target and "ywok" in sample_target:
            self._from_wok()
        elif "alpha" in sample_target and "beta" in sample_target:
            self._from_positioner()
        else:
            raise ValueError("Target data does not contain the necessary columns.")

        # Final validation.
        self.validate()

    def _from_wok(self):
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
                zwok=zwok,
            )

            self.target_data[hole_id].update({"alpha": alpha, "beta": beta})

        # Now simply call _from_positioner()
        self._from_positioner()

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
                        target.get("zwok", -999.0),
                    ]
                )
            else:
                wok_coords.append([-999.0] * 3)
        self.fibre_table[["xwok", "ywok", "zwok"]] = wok_coords

    def _from_positioner(self):
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
                        assigned=assigned,
                        on_target=assigned,
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
                            "assigned": assigned,
                            "on_target": assigned,
                        }
                    )
                    data[(pid, ftype)] = row

        # Now do a single update of the whole fibre table.
        self.fibre_table.update(pandas.DataFrame.from_dict(data, orient="index"))

    def _check_all_assigned(self):
        """Check that all the positioners are in ``target_data``."""

        if len(set(self.target_data) - set(self.wok_data.holeID)) > 0:
            raise ValueError("Not all the positioners have been assigned a target.")
