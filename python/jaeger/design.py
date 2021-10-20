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

from typing import Optional, Union, cast

import numpy
import pandas
import peewee
from astropy.table import Table
from astropy.time import Time
from coordio import (
    ICRS,
    Field,
    FocalPlane,
    Observed,
    PositionerApogee,
    PositionerBoss,
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
from pydl.pydlutils.yanny import write_ndarray_to_yanny

from sdssdb.peewee.sdss5db import opsdb, targetdb

from jaeger import config, log
from jaeger.exceptions import JaegerError, JaegerUserWarning
from jaeger.utils import get_goto_move_time


PositionerType = Union[PositionerApogee, PositionerBoss]


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

        log.debug(f"[Design]: loading design {design_id}.")

        self.design_id = design_id

        try:
            self.design = targetdb.Design.get(design_id=design_id)
        except peewee.DoesNotExist:
            raise ValueError(f"design_id {design_id} does not exist in the database.")

        self.field = self.design.field
        self.assignments: list[targetdb.Assignment] = list(self.design.assignments)

        log.debug(f"[Design]: creating initial configuration for {design_id}.")

        self.configuration = Configuration(self)

        log.debug("[Design]: finished creating initial configuration.")

    def __repr__(self):
        return f"<Design (design_id={self.design_id})>"


class Configuration:
    """A configuration based on a design."""

    def __init__(self, design: Design):

        # Configuration ID is None until we insert in the database.
        # Once set, it cannot be changed.
        self.configuration_id: int | None = None

        self.design = design
        self.assignment_data = AssignmentData(self)

        assert self.assignment_data.site.time
        self.epoch = self.assignment_data.site.time.jd

    def __repr__(self):
        return (
            f"<Configuration (configuration_id={self.configuration_id}"
            f"design_id={self.design.design_id})>"
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

    def get_trajectory(self, current_positions: dict[int, tuple[float, float]]):
        """Returns a trajectory dictionary based on the current position."""

        # TODO: not calling kaiju yet because there seem to be several sets
        # of calibration files. For now just using a variation of goto.

        default_speed = config["positioner"]["motor_speed"]
        speed = (default_speed, default_speed)

        trajectories = {}
        for pid in current_positions:
            current_alpha, current_beta = current_positions[pid]

            trajectories[pid] = {
                "alpha": [(current_alpha, 0.1)],
                "beta": [(current_beta, 0.1)],
            }

            if pid in self.assignment_data.positioner_ids:
                pindex = self.assignment_data.positioner_to_index[pid]

                if pindex not in self.assignment_data.valid_index:
                    warnings.warn(
                        f"Coordinates for positioner {pid} "
                        "are not valid. Not moving it.",
                        JaegerUserWarning,
                    )
                    trajectories[pid]["alpha"].append((current_alpha, 0.2))
                    trajectories[pid]["beta"].append((current_beta, 0.2))
                    continue

                alpha_end, beta_end = self.assignment_data.positioner[pindex]

                alpha_delta = abs(alpha_end - current_alpha)
                beta_delta = abs(beta_end - current_beta)

                time_end = [
                    get_goto_move_time(alpha_delta, speed=speed[0]),
                    get_goto_move_time(beta_delta, speed=speed[1]),
                ]

                trajectories[pid]["alpha"].append((alpha_end, time_end[0] + 0.1))
                trajectories[pid]["beta"].append((beta_end, time_end[1] + 0.1))

            else:
                warnings.warn(
                    f"Positioner {pid} is not assigned in this design. "
                    "Not moving it.",
                    JaegerUserWarning,
                )
                trajectories[pid]["alpha"].append((current_alpha, 0.2))
                trajectories[pid]["beta"].append((current_beta, 0.2))

        return trajectories

    def write_to_database(self, replace=False):
        """Writes the configuration to the database."""

        if self.configuration_id is None:

            with opsdb.database.atomic():
                configuration = opsdb.Configuration(
                    configuration_id=self.configuration_id,
                    design_id=self.design.design_id,
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
        # print(focals)
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
                warnings.warn(
                    f"Summary file {os.path.basename(path)} exists. Overwriting it.",
                    JaegerUserWarning,
                )
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


class AssignmentData:
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

        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.design.design_id

        log.debug(
            f"[AssignmentData]: creating assignment data for design {self.design_id}"
        )

        self.observatory: str = self.design.field.observatory.label.upper()
        self.site = Site(self.observatory)

        positioner_table = positionerTable.set_index("holeID")
        wok_table = wokCoords.set_index("holeID")

        self.assignments = [
            assg
            for assg in self.design.assignments
            if assg.instrument.label.lower()  # TODO: remove this when RS matches wok.
            in wok_table.loc[assg.hole.holeid].holeType.lower()
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
            warnings.warn(
                "Some coordinates failed while converting to "
                "positioner coordinates. Skipping.",
                JaegerUserWarning,
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
