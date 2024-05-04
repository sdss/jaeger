#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-26
# @Filename: coordinates.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from functools import cache

from typing import TYPE_CHECKING, Any, Mapping

import nptyping as npt
import numpy
import polars
from astropy.time import Time

from coordio import ICRS, Field, FocalPlane, Observed, Site, Wok
from coordio.conv import (
    positionerToTangent,
    positionerToWok,
    tangentToPositioner,
    tangentToWok,
    wokToPositioner,
    wokToTangent,
)
from coordio.defaults import INST_TO_WAVE, POSITIONER_HEIGHT, getHoleOrient

from jaeger.target.tools import get_wok_data


if TYPE_CHECKING:
    BoresightType = (
        npt.NDArray[npt.Shape["2"], npt.Float64] | tuple[float, float] | Observed
    )


__all__ = [
    "positioner_from_icrs_dataframe",
    "icrs_from_positioner_dataframe",
    "wok_to_positioner",
]


def positioner_from_icrs_dataframe(
    data: polars.DataFrame | Mapping[str, Any],
    boresight: BoresightType,
    site: str | Site,
    epoch: float | None = None,
    position_angle: float = 0.0,
    focal_plane_scale: float | None = None,
):
    """Returns the positioner coordinates from ICRS coordinates.

    Parameters
    ----------
    data
        A data frame with ICRS data or a mapping that can be converted into a
        data frame. The data frame must contain columns ``positioner_id``,
        ``hole_id``, ``fibre_type``, ``wavelength``, ``ra_icrs``, and ``dec_icrs``.
        Additional columns that will be used if available are ``pmra``, ``pmdec``,
        ``parallax``, ``coord_epoch``, ``delta_ra`` and  ``delta_dec``.
    boresight
        The RA/Dec coordinates of the field boresight, or an ``Observed``
        instance.
    site
        Either a ``coordio`` site object of the observatory.
    epoch
        The JD of the epoch to which the coordinates should be propagated.
    position_angle
        The position angle of the instrument.
    focal_plane_scale
        The focal plane scale.

    Returns
    -------
    positioner_dataframe
        A data frame with the positioner coordinates (columns ``alpha`` and ``beta``)
        and intermediate coordinates.

    """

    if not isinstance(data, polars.DataFrame):
        data = polars.DataFrame(data)

    data = data.clone()

    # Check that the required columns are present.
    required_cols = [
        "ra_icrs",
        "dec_icrs",
        "hole_id",
        "positioner_id",
        "fibre_type",
        "wavelength",
    ]
    if not set(required_cols).issubset(data.columns):
        raise ValueError(f"data must contain columns {required_cols!r}.")

    if data.select(polars.col(required_cols)).null_count().transpose().sum()[0, 0] > 0:
        raise ValueError("Missing values in required columns.")

    # Create site.
    if isinstance(site, str):
        assert site in ["APO", "LCO"], 'Invalid observatory. Must be "APO" or "LCO".'
        site = Site(site)

    # Override the site epoch if passed.
    if epoch is not None:
        site.set_time(epoch)

    if site.time is None:
        raise ValueError("Site epoch must be set.")

    # Add optional columns if they don't exist.
    opt_cols = {
        "pmra": polars.lit(0.0, dtype=polars.Float32),
        "pmdec": polars.lit(0.0, dtype=polars.Float32),
        "parallax": polars.lit(0.0, dtype=polars.Float32),
        "coord_epoch": polars.lit(None, dtype=polars.Float32),
        "delta_ra": polars.lit(0.0, dtype=polars.Float32),
        "delta_dec": polars.lit(0.0, dtype=polars.Float32),
    }
    data = data.with_columns(
        **{k: v for k, v in opt_cols.items() if k not in data.columns}
    )

    # Fill columns with zeros.
    data = data.with_columns(polars.col(list(opt_cols)).fill_null(0.0).fill_nan(0.0))

    # Calculate offset coordinates.
    cos_dec = (polars.col("dec_icrs") * numpy.pi / 180).cos()
    data = data.with_columns(
        ra_offset=polars.col("ra_icrs") + polars.col("delta_ra") / 3600.0 / cos_dec,
        dec_offset=polars.col("dec_icrs") + polars.col("delta_dec") / 3600.0,
    )

    # Create the Boresight object.
    if boresight is not None and not isinstance(boresight, Observed):
        boresight = Observed(
            ICRS(numpy.array([boresight])),
            site=site,
            wavelength=INST_TO_WAVE["GFA"],
        )

    wavelength = data["wavelength"].to_numpy()

    icrs = ICRS(
        data[["ra_offset", "dec_offset"]].to_numpy(),
        pmra=data["pmra"].to_numpy(),
        pmdec=data["pmdec"].to_numpy(),
        parallax=data["parallax"].to_numpy(),
        epoch=Time(data["coord_epoch"].to_numpy(), format="jyear").jd,
    )

    icrs_epoch = icrs.to_epoch(site.time.jd, site=site)

    observed = Observed(icrs_epoch, wavelength=wavelength, site=site)
    field = Field(observed, field_center=boresight)
    focal = FocalPlane(
        field,
        wavelength=wavelength,
        site=site,
        fpScale=focal_plane_scale,
        use_closest_wavelength=True,
    )
    wok = Wok(focal, site=site, obsAngle=position_angle)

    # Add the newly calculated coordinates to the data frame.
    data = data.with_columns(
        ra_epoch=polars.Series(icrs_epoch[:, 0]),
        dec_epoch=polars.Series(icrs_epoch[:, 1]),
        ra_observed=polars.Series(observed.ra),
        dec_observed=polars.Series(observed.dec),
        alt_observed=polars.Series(observed[:, 0]),
        az_observed=polars.Series(observed[:, 1]),
        xfocal=polars.Series(focal[:, 0]),
        yfocal=polars.Series(focal[:, 1]),
        xwok=polars.Series(wok[:, 0]),
        ywok=polars.Series(wok[:, 1]),
        zwok=polars.Series(wok[:, 2]),
    ).sort("positioner_id")

    # Get the wok data and cast the positionerID to int32.
    wok_data = get_wok_data(site.name)
    wok_data = wok_data.with_columns(polars.col.positionerID.cast(polars.Int32))

    # Left join with the input data so that we have one wok data entry per fibre.
    data_to_wok = data.join(
        wok_data,
        how="left",
        left_on="positioner_id",
        right_on="positionerID",
    )

    # Create columns for the beta coordinates depending on the fibre type.
    data_to_wok = data_to_wok.with_columns(
        xBeta=polars.when(polars.col.fibre_type == "APOGEE")
        .then(polars.col.apX)
        .when(polars.col.fibre_type == "BOSS")
        .then(polars.col.bossX)
        .when(polars.col.fibre_type == "Metrology")
        .then(polars.col.metX)
        .otherwise(None),
        yBeta=polars.when(polars.col.fibre_type == "APOGEE")
        .then(polars.col.apY)
        .when(polars.col.fibre_type == "BOSS")
        .then(polars.col.bossY)
        .when(polars.col.fibre_type == "Metrology")
        .then(polars.col.metY)
        .otherwise(None),
    )

    # Calculate alpha and beta coordinates.
    alphas, betas = wokToPositioner(
        data_to_wok["xwok"].to_numpy(),
        data_to_wok["ywok"].to_numpy(),
        data_to_wok["zwok"].to_numpy(),
        data_to_wok["xBeta"].to_numpy(),
        data_to_wok["yBeta"].to_numpy(),
        data_to_wok["alphaArmLen"].to_numpy(),
        data_to_wok["alphaOffset"].to_numpy(),
        data_to_wok["betaOffset"].to_numpy(),
        data_to_wok[["xWok", "yWok", "zWok"]].to_numpy(),
        data_to_wok[["ix", "iy", "iz"]].to_numpy(),
        data_to_wok[["jx", "jy", "jz"]].to_numpy(),
        data_to_wok[["kx", "ky", "kz"]].to_numpy(),
        data_to_wok["dx"].to_numpy(),
        data_to_wok["dy"].to_numpy(),
    )

    data = data.with_columns(
        alpha=polars.Series(alphas, dtype=polars.Float64),
        beta=polars.Series(betas, dtype=polars.Float64),
    )

    return data


def icrs_from_positioner_dataframe(
    data: polars.DataFrame | Mapping[str, Any],
    site: str | Site,
    boresight: BoresightType | None = None,
    epoch: float | None = None,
    position_angle: float = 0.0,
    focal_plane_scale: float | None = None,
):
    """Returns the positioner coordinates from ICRS coordinates.

    Parameters
    ----------
    data
        A data frame with positioner coordinates or a mapping that can be
        converted into a data frame. The data frame must contain columns
        ``positioner_id``, ``hole_id``, ``fibre_type``, ``wavelength``, ``alpha``,
        and ``beta``.
    site
        Either a ``coordio`` site object of the observatory.
    boresight
        The RA/Dec coordinates of the field boresight, or an ``Observed``
        instance.
    epoch
        The JD of the epoch to which the coordinates should be propagated.
    position_angle
        The position angle of the instrument.
    focal_plane_scale
        The focal plane scale.

    Returns
    -------
    positioner_dataframe
        A data frame with the ICRS coordinates calculated from alpha/beta and all
        the intermediate coordinates.

    """

    if not isinstance(data, polars.DataFrame):
        data = polars.DataFrame(data)

    data = data.clone()

    # Check that the required columns are present.
    required_cols = [
        "alpha",
        "beta",
        "hole_id",
        "positioner_id",
        "fibre_type",
        "wavelength",
    ]
    if not set(required_cols).issubset(data.columns):
        raise ValueError(f"data must contain columns {required_cols!r}.")

    if data.select(polars.col(required_cols)).null_count().transpose().sum()[0, 0] > 0:
        raise ValueError("Missing values in required columns.")

    data = data.sort("positioner_id")

    # Create site.
    if isinstance(site, str):
        assert site in ["APO", "LCO"], 'Invalid observatory. Must be "APO" or "LCO".'
        site = Site(site)

    # Override the site epoch if passed.
    if epoch is not None:
        site.set_time(epoch)

    if site.time is None:
        raise ValueError("Site epoch must be set.")

    # Create the Boresight object.
    if boresight is not None and not isinstance(boresight, Observed):
        boresight = Observed(
            ICRS(numpy.array([boresight])),
            site=site,
            wavelength=INST_TO_WAVE["GFA"],
        )

    wavelength = data["wavelength"].to_numpy()

    # Get the wok data and cast the positionerID to int32.
    wok_data = get_wok_data(site.name)
    wok_data = wok_data.with_columns(polars.col.positionerID.cast(polars.Int32))

    # Left join with the input data so that we have one wok data entry per fibre.
    data_to_wok = data.join(
        wok_data,
        how="left",
        left_on="positioner_id",
        right_on="positionerID",
    )

    # Create columns for the beta coordinates depending on the fibre type.
    data_to_wok = data_to_wok.with_columns(
        xBeta=polars.when(polars.col.fibre_type == "APOGEE")
        .then(polars.col.apX)
        .when(polars.col.fibre_type == "BOSS")
        .then(polars.col.bossX)
        .when(polars.col.fibre_type == "Metrology")
        .then(polars.col.metX)
        .otherwise(None),
        yBeta=polars.when(polars.col.fibre_type == "APOGEE")
        .then(polars.col.apY)
        .when(polars.col.fibre_type == "BOSS")
        .then(polars.col.bossY)
        .when(polars.col.fibre_type == "Metrology")
        .then(polars.col.metY)
        .otherwise(None),
    )

    # Get the wok coordinates.
    xwok, ywok, zwok = positionerToWok(
        data_to_wok["alpha"].to_numpy(),
        data_to_wok["beta"].to_numpy(),
        data_to_wok["xBeta"].to_numpy(),
        data_to_wok["yBeta"].to_numpy(),
        data_to_wok["alphaArmLen"].to_numpy(),
        data_to_wok["alphaOffset"].to_numpy(),
        data_to_wok["betaOffset"].to_numpy(),
        data_to_wok[["xWok", "yWok", "zWok"]].to_numpy(),
        data_to_wok[["ix", "iy", "iz"]].to_numpy(),
        data_to_wok[["jx", "jy", "jz"]].to_numpy(),
        data_to_wok[["kx", "ky", "kz"]].to_numpy(),
        data_to_wok["dx"].to_numpy(),
        data_to_wok["dy"].to_numpy(),
    )

    focal = FocalPlane(
        Wok(
            numpy.array([xwok, ywok, zwok]).T,
            site=site,
            obsAngle=position_angle,
        ),
        wavelength=wavelength,
        site=site,
        fpScale=focal_plane_scale,
        use_closest_wavelength=True,
    )

    if boresight is not None:
        field = Field(focal, field_center=boresight)
        obs = Observed(field, site=site, wavelength=wavelength)
        icrs = ICRS(obs)
    else:
        field = obs = icrs = None

    # Add focal coordinates. These always exist.
    data = data.with_columns(
        xfocal=polars.Series(focal[:, 0]),
        yfocal=polars.Series(focal[:, 1]),
    )

    # The field, observed, ICRS coordinates depend on whether there is a boresight.
    if icrs is not None and obs is not None and field is not None:
        data = data.with_columns(
            ra_epoch=polars.Series(icrs[:, 0], dtype=polars.Float64),
            dec_epoch=polars.Series(icrs[:, 1], dtype=polars.Float64),
            ra_observed=polars.Series(obs.ra, dtype=polars.Float64),
            dec_observed=polars.Series(obs.dec, dtype=polars.Float64),
            alt_observed=polars.Series(obs[:, 0], dtype=polars.Float64),
            az_observed=polars.Series(obs[:, 1], dtype=polars.Float64),
        )
    else:
        data = data.with_columns(
            ra_epoch=polars.lit(None, dtype=polars.Float64),
            dec_epoch=polars.lit(None, dtype=polars.Float64),
            ra_observed=polars.lit(None, dtype=polars.Float64),
            dec_observed=polars.lit(None, dtype=polars.Float64),
            alt_observed=polars.lit(None, dtype=polars.Float64),
            az_observed=polars.lit(None, dtype=polars.Float64),
        )

    return data


@cache
def get_hole_orient(site: str, hole_id: str):
    """A cached version of ``coordio.defaults.getHoleOrient``."""

    return get_hole_orient(site, hole_id)


def wok_to_positioner(
    hole_id: str,
    site: str,
    fibre_type: str,
    xwok: float,
    ywok: float,
    zwok: float = POSITIONER_HEIGHT,
    wok_data: polars.DataFrame | None = None,
) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Converts from wok to positioner coordinates.

    Returns arrays with the positioner and tangent coordinates.

    """

    if wok_data is None:
        wok_data = get_wok_data(site)

    positioner_data = wok_data.filter(
        polars.col("holeID") == hole_id,
        polars.col("site") == site,
    )

    hole_orient = getHoleOrient(site, hole_id)

    if fibre_type == "APOGEE":
        xBeta = positioner_data[0, "apX"]
        yBeta = positioner_data[0, "apY"]
    elif fibre_type == "BOSS":
        xBeta = positioner_data[0, "bossX"]
        yBeta = positioner_data[0, "bossY"]
    elif fibre_type == "Metrology":
        xBeta = positioner_data[0, "metX"]
        yBeta = positioner_data[0, "metY"]
    else:
        raise ValueError(f"Invalid fibre type {fibre_type}.")

    tangent = wokToTangent(
        xwok,
        ywok,
        zwok,
        *hole_orient,
        dx=positioner_data[0, "dx"],
        dy=positioner_data[0, "dy"],
    )

    alpha, beta, _ = tangentToPositioner(
        tangent[0][0],
        tangent[1][0],
        xBeta,
        yBeta,
        la=positioner_data[0, "alphaArmLen"],
        alphaOffDeg=positioner_data[0, "alphaOffset"],
        betaOffDeg=positioner_data[0, "betaOffset"],
    )

    return (
        numpy.array([alpha, beta]),
        numpy.array([tangent[0][0], tangent[1][0], tangent[2][0]]),
    )


def positioner_to_wok(
    hole_id: str,
    site: str,
    fibre_type: str,
    alpha: float,
    beta: float,
    wok_data: polars.DataFrame | None = None,
):
    """Convert from positioner to wok coordinates.

    Returns xyz wok and tangent coordinates as a tuple of arrays.

    """

    if wok_data is None:
        wok_data = get_wok_data(site)

    positioner_data = wok_data.filter(
        polars.col("holeID") == hole_id,
        polars.col("site") == site,
    )

    b = positioner_data[0, ["xWok", "yWok", "zWok"]]
    iHat = positioner_data[0, ["ix", "iy", "iz"]]
    jHat = positioner_data[0, ["jx", "jy", "jz"]]
    kHat = positioner_data[0, ["kx", "ky", "kz"]]

    if fibre_type == "APOGEE":
        xBeta = positioner_data[0, "apX"]
        yBeta = positioner_data[0, "apY"]
    elif fibre_type == "BOSS":
        xBeta = positioner_data[0, "bossX"]
        yBeta = positioner_data[0, "bossY"]
    elif fibre_type == "Metrology":
        xBeta = positioner_data[0, "metX"]
        yBeta = positioner_data[0, "metY"]
    else:
        raise ValueError(f"Invlid fibre type {fibre_type}.")

    tangent = positionerToTangent(
        alpha,
        beta,
        xBeta,
        yBeta,
        la=positioner_data[0, "alphaArmLen"],
        alphaOffDeg=positioner_data[0, "alphaOffset"],
        betaOffDeg=positioner_data[0, "betaOffset"],
    )

    wok = tangentToWok(
        tangent[0],
        tangent[1],
        0,
        b,
        iHat,
        jHat,
        kHat,
        dx=positioner_data[0, "dx"],
        dy=positioner_data[0, "dy"],
    )

    wok_coords = numpy.array(wok)

    return wok_coords, numpy.array([tangent[0], tangent[1], 0])
