#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-26
# @Filename: assignment.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from functools import cache

from typing import TYPE_CHECKING, Any, Literal, Mapping, Optional

import numpy
import polars

from coordio import (
    ICRS,
    Observed,
    Site,
)
from coordio.defaults import (
    INST_TO_WAVE,
    VALID_WAVELENGTHS,
)

from jaeger import config, log
from jaeger.target.coordinates import (
    icrs_from_positioner_dataframe,
    positioner_from_icrs_dataframe,
)
from jaeger.target.schemas import FIBRE_DATA_SCHEMA

from .tools import get_wok_data


if TYPE_CHECKING:
    from .configuration import Configuration, DitheredConfiguration, ManualConfiguration


__all__ = ["Assignment", "BaseAssignment", "ManualAssignment"]


NewPositionsType = Mapping[int, Mapping[Literal["alpha", "beta"], float | None]]


class BaseAssignment:
    """Information about the target assignment along with coordinate transformation."""

    def __init__(
        self,
        configuration: Configuration | ManualConfiguration | DitheredConfiguration,
        observatory: Optional[str] = None,
        scale: float | None = None,
        boss_wavelength: float | None = None,
        apogee_wavelength: float | None = None,
    ):
        self.configuration = configuration

        self.design = configuration.design
        self.design_id = self.configuration.design_id

        self.boss_wavelength = boss_wavelength or INST_TO_WAVE["Boss"]
        self.apogee_wavelength = apogee_wavelength or INST_TO_WAVE["Apogee"]

        self.observatory: str
        if observatory is None:
            if self.design is None:
                raise ValueError("Cannot determine observatory.")
            self.observatory = self.design.field.observatory.upper()
        else:
            self.observatory = observatory

        self.site = Site(self.observatory)
        self.site.set_time()

        self.scale = scale or 1.0

        self.wok_data = get_wok_data(self.observatory)

        if self.design:
            self.target_data = self.design.target_data
            self.position_angle = self.design.field.position_angle
        else:
            self.target_data = {}
            self.position_angle = 0.0

        self.boresight: Observed | None = None

        self.fibre_data = self.create_fibre_data()

    def __repr__(self):
        return f"<{self.__class__.__name__} (design_id={self.design_id})>"

    @property
    def epoch(self):
        """Returns the configuratione epoch."""

        return self.configuration.epoch

    def compute_coordinates(self, epoch: Optional[float] = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        epoch
            The Julian Date for which to compute the coordinates.

        """

        raise NotImplementedError("Must be overridden by subclasses.")

    @cache
    def get_wavelength(self, fibre_type: str):
        """Returns the wavelength for a given fibre type."""

        if fibre_type == "APOGEE":
            return self.apogee_wavelength
        elif fibre_type == "BOSS":
            return self.boss_wavelength
        elif fibre_type == "Metrology":
            return INST_TO_WAVE["GFA"]

        raise ValueError(f"Invalid fibre type {fibre_type!r}.")

    def create_fibre_data(self):
        """Creates an empty fibre table."""

        # Manually create a list of dictionaries with the target data. This includes
        # one entry per fibre (APOGEE, BOSS, Metrology). If that specific fibre has
        # a target assigned, the target data is used.
        fibre_tdata: list[dict[str, Any]] = []
        tdata = self.target_data

        for wrow in self.wok_data.iter_rows(named=True):
            hole_id = wrow["holeID"]
            positioner_id = wrow["positionerID"]

            for fibre_type in ["APOGEE", "BOSS", "Metrology"]:
                if fibre_type == "BOSS":
                    fibre_id = wrow["BOSSFiber"]
                elif fibre_type == "APOGEE":
                    fibre_id = wrow["APOGEEFiber"]
                else:
                    fibre_id = None

                hole_data = {
                    "positioner_id": positioner_id,
                    "fibre_type": fibre_type,
                    "hole_id": hole_id,
                    "fibre_id": fibre_id,
                    "site": self.observatory,
                    "wavelength": self.get_wavelength(fibre_type),
                    "catalogid": None,
                    "ra_icrs": None,
                    "dec_icrs": None,
                    "pmra": None,
                    "pmdec": None,
                    "parallax": None,
                    "coord_epoch": None,
                    "delta_ra": None,
                    "delta_dec": None,
                    "assigned": False,
                }

                if hole_id in tdata and tdata[hole_id]["fibre_type"] == fibre_type:
                    hole_data.update(
                        {
                            "catalogid": tdata[hole_id]["catalogid"],
                            "ra_icrs": tdata[hole_id]["ra"],
                            "dec_icrs": tdata[hole_id]["dec"],
                            "pmra": tdata[hole_id]["pmra"],
                            "pmdec": tdata[hole_id]["pmdec"],
                            "parallax": tdata[hole_id]["parallax"],
                            "coord_epoch": tdata[hole_id]["epoch"],
                            "delta_ra": tdata[hole_id]["delta_ra"],
                            "delta_dec": tdata[hole_id]["delta_dec"],
                            "assigned": True,
                        }
                    )

                fibre_tdata.append(hole_data)

        # Create initial DF from wok_data. This contains 1500 columns, one per fibre.
        fibre_data = polars.DataFrame(fibre_tdata)

        # Add empty columns for the rest of the schema. Negate boolean columns.
        fibre_data = (
            fibre_data.with_columns(
                [
                    polars.lit(None).alias(str(column_name))
                    for column_name in FIBRE_DATA_SCHEMA
                    if column_name not in fibre_data.columns
                ]
            )
            .select(polars.col(map(str, FIBRE_DATA_SCHEMA)))  # Reorder
            .cast(FIBRE_DATA_SCHEMA)
            .with_columns(polars.selectors.boolean().fill_null(False))
            .sort("hole_id", "fibre_type")
            .with_columns(index=polars.arange(fibre_data.height, dtype=polars.Int32))
        )

        # Check if we have non-standard wavelengths.
        ft_valid_wvl = fibre_data.filter(polars.col.wavelength.is_in(VALID_WAVELENGTHS))
        if len(ft_valid_wvl) < len(fibre_data):
            log.warning(
                "Using non-default wavelengths. "
                "Focal plane transformation may be sub-optimal."
            )

        return fibre_data

    def update_positioner_coordinates(
        self,
        new_positions: NewPositionsType,
        validate: bool = True,
    ):
        """Updates alpha/beta values and recalculates upstream coordinates.

        Parameters
        ----------
        new_positions
            A mapping of positioner ID to a dictionary containing the new alpha
            and beta positions.
        validate
            Whether to validate the fibre data after updating the positions.

        """

        alpha0, beta0 = config["kaiju"]["lattice_position"]
        fdata = self.fibre_data

        alphas: list[float] = []
        betas: list[float] = []
        invalid: list[int] = []
        for row in fdata.iter_rows(named=True):
            positioner_id = row["positioner_id"]
            if positioner_id in new_positions:
                alpha = new_positions[positioner_id]["alpha"]
                beta = new_positions[positioner_id]["beta"]
                if alpha is None or beta is None:
                    alpha = alpha0
                    beta = beta0
                    invalid.append(positioner_id)

                alphas.append(alpha)
                betas.append(beta)
            else:
                alphas.append(row["alpha"])
                betas.append(row["beta"])

        # Update alpha/beta.
        fdata = fdata.with_columns(
            alpha=polars.Series(alphas, dtype=polars.Float64),
            beta=polars.Series(betas, dtype=polars.Float64),
        )

        # We have set the positioners without alpha/beta to the lattice positions,
        # but these robots are invalid and probably disabled(?).
        fdata = fdata.with_columns(
            valid=polars.when(polars.col.positioner_id.is_in(invalid))
            .then(False)
            .otherwise(polars.col.valid)
        )

        # Get the subframe with new coordinates.
        fdata_new = fdata.filter(polars.col.positioner_id.is_in(list(new_positions)))

        # Get updated upstream coordinates.
        fdata_new_icrs = icrs_from_positioner_dataframe(
            fdata_new,
            self.site,
            boresight=self.boresight,
            epoch=self.epoch,
            position_angle=self.position_angle,
        ).cast(FIBRE_DATA_SCHEMA)

        # Update fdata for the new coordinates.
        fdata = polars.concat([fdata_new_icrs, fdata]).group_by("index").first()

        self.fibre_data = fdata.sort("index")

        if validate:
            self.validate()

        return fdata_new_icrs.sort("index")

    def validate(self):
        """Validates fibre data."""

        na = (
            polars.col.alpha.is_null()
            | polars.col.beta.is_null()
            | polars.col.alpha.is_nan()
            | polars.col.beta.is_nan()
        )
        over_180 = polars.col.beta > 180

        self.fibre_data = self.fibre_data.with_columns(
            valid=polars.when(na | over_180).then(False).otherwise(True),
            on_target=polars.when(polars.col.on_target.not_() | na | over_180)
            .then(False)
            .otherwise(True),
        )


class Assignment(BaseAssignment):
    """Assignment data from a valid design with associated target information."""

    def __init__(
        self,
        configuration: Configuration | DitheredConfiguration,
        epoch: float | None = None,
        compute_coordinates: bool = True,
        scale: float | None = None,
        boss_wavelength: float | None = None,
        apogee_wavelength: float | None = None,
    ):
        super().__init__(
            configuration,
            scale=scale,
            boss_wavelength=boss_wavelength,
            apogee_wavelength=apogee_wavelength,
        )

        if compute_coordinates:
            self.compute_coordinates(epoch)

    def compute_coordinates(self, epoch: Optional[float] = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        epoch
            The Julian Date for which to compute the coordinates. If not
            provided, the configuration epoch is used.

        """

        alpha0, beta0 = config["kaiju"]["lattice_position"]

        if not self.design:
            raise ValueError("Cannot compute coordinates without a design.")

        if epoch:
            self.site.set_time(epoch)
            self.configuration.epoch = epoch

        icrs_bore = ICRS([[self.design.field.racen, self.design.field.deccen]])
        self.boresight = Observed(
            icrs_bore,
            site=self.site,
            wavelength=INST_TO_WAVE["GFA"],
        )

        # Select only the rows with valid ICRS coordinates.
        fibre_data_icrs = self.fibre_data.filter(
            polars.col("ra_icrs").is_not_null(),
            polars.col("dec_icrs").is_not_null(),
        )

        # Get the positioner data for the targets with ICRS. The returned
        # dataframe has the same height and all the same columns, but populated.
        pos_data = positioner_from_icrs_dataframe(
            fibre_data_icrs,
            self.boresight,
            self.site,
            epoch=epoch,
            position_angle=self.design.field.position_angle,
            focal_plane_scale=self.scale,
        )

        # A couple sanity checks.
        assert pos_data.height == fibre_data_icrs.height
        assert pos_data.columns == fibre_data_icrs.columns

        # We mark these fibres as "assigned".
        pos_data = pos_data.with_columns(assigned=True, on_target=True)

        # Now get a data frame with the fibres that are not assigned.
        unassigned = self.fibre_data.filter(
            polars.col("index").is_in(fibre_data_icrs["index"]).not_()
        )

        # For these the positioner alpha/beta coordinates are the same as for the
        # positioner with the same positioner_id in pos_data. We create lists
        # of the same height as unassinged and then add them.
        alpha_unassigned: list[float] = []
        beta_unassigned: list[float] = []
        for row in unassigned.iter_rows(named=True):
            pid_data = pos_data.filter(polars.col.positioner_id == row["positioner_id"])
            if pid_data.height == 0:
                alpha_unassigned.append(numpy.nan)
                beta_unassigned.append(numpy.nan)
            else:
                alpha_unassigned.append(pid_data["alpha"][0])
                beta_unassigned.append(pid_data["beta"][0])

        unassigned = unassigned.with_columns(
            alpha=polars.Series(alpha_unassigned),
            beta=polars.Series(beta_unassigned),
        )

        # If a fibre has alpha/beta NaN that means that it was not assigned.
        # We set these to the folded alpha/beta positions.
        when = polars.when(polars.col.alpha.is_null(), polars.col.beta.is_null())
        unassigned = unassigned.with_columns(
            alpha=when.then(alpha0).otherwise(polars.col.alpha),
            beta=when.then(beta0).otherwise(polars.col.beta),
        )

        # Now we calculate the upstream coordinates for the unassigned fibres.
        icrs_unassigned = icrs_from_positioner_dataframe(
            unassigned,
            self.site,
            boresight=self.boresight,
            epoch=epoch,
            position_angle=self.design.field.position_angle,
            focal_plane_scale=self.scale,
        )
        icrs_unassigned = icrs_unassigned.with_columns(assigned=False, on_target=False)

        # Join the two dataframes. Recast and resort.
        fibre_data = (
            polars.concat([pos_data, icrs_unassigned])
            .sort("index")
            .cast(FIBRE_DATA_SCHEMA)
        )

        self.fibre_data = fibre_data

        # Mark all the fibres as valid for now. .validate() will set them to False
        # where appropriate.
        self.fibre_data = self.fibre_data.with_columns(valid=True)

        self.validate()


class ManualAssignment(BaseAssignment):
    """Assignment data from a manual configuration.

    Parameters
    ----------
    configuration
        The parent `.ManualConfiguration`.
    positions
        A dictionary containing the targeting information. It must be a
        mapping of hole ID to a tuple of ``alpha`` and ``beta`` positions.
    observatory
        The observatory name.
    field_centre
        A tuple or array with the boresight coordinates as current epoch
        RA/Dec. This is used to determine the on-sky position seen by each fibre.
    position_angle
        The position angle of the field.
    scale
        The focal plane scale factor.

    """

    boresight: Observed | None = None

    def __init__(
        self,
        configuration: ManualConfiguration,
        positions: dict[int, tuple[float | None, float | None]],
        observatory: str,
        field_centre: tuple[float, float] | numpy.ndarray | None = None,
        position_angle: float = 0.0,
        scale: float | None = None,
    ):
        super().__init__(configuration, observatory=observatory, scale=scale)

        self.positions = positions

        if field_centre is not None:
            self.field_centre = numpy.array(field_centre)
        else:
            self.field_centre = None

        self.position_angle = position_angle

        self.fibre_data = self.create_fibre_data()

        self.compute_coordinates()

    def compute_coordinates(self, epoch: float | None = None):
        """Computes coordinates in different systems.

        Parameters
        ----------
        epoch
            The Julian Date for which to compute the coordinates. If not
            provided, the configuration epoch is used.

        """

        alpha0, beta0 = config["kaiju"]["lattice_position"]

        fdata = self.fibre_data.clone()

        if epoch:
            self.site.set_time(epoch)
            self.configuration.epoch = epoch

        assert self.site.time

        if self.field_centre:
            icrs_bore = ICRS([self.field_centre])
            self.boresight = Observed(
                icrs_bore,
                site=self.site,
                wavelength=INST_TO_WAVE["GFA"],
            )

        if len(fdata) == 0:
            raise ValueError("Fibre data has zero length.")

        alphas: list[float] = []
        betas: list[float] = []
        for row in fdata.iter_rows(named=True):
            positioner_id = row["positioner_id"]
            if positioner_id not in self.positions:
                alpha = alpha0
                beta = beta0
            elif None in self.positions[positioner_id]:
                alpha = alpha0
                beta = beta0
            else:
                alpha, beta = self.positions[positioner_id]

            assert alpha is not None and beta is not None
            alphas.append(alpha)
            betas.append(beta)

        fdata = fdata.with_columns(
            alpha=polars.Series(alphas),
            beta=polars.Series(betas),
            valid=True,
        )

        fdata = (
            icrs_from_positioner_dataframe(
                fdata,
                self.site,
                boresight=self.boresight,
                epoch=self.site.time.jd,
                position_angle=self.position_angle,
            )
            .sort("index")
            .cast(FIBRE_DATA_SCHEMA)
        )

        # Mark the metrology fibre as assigned. A hack so that get_paths() works.
        fdata = fdata.with_columns(
            assigned=polars.when(polars.col.fibre_type == "Metrology")
            .then(True)
            .otherwise(False)
        )

        self.fibre_data = fdata

        # Final validation.
        self.validate()
