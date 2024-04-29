#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-05-04
# @Filename: plotting.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import pathlib

from typing import TYPE_CHECKING

import matplotlib.pyplot as plt
import numpy
import polars
import seaborn

from jaeger.target.configuration import BaseConfiguration


if TYPE_CHECKING:
    from matplotlib.axes import Axes


__all__ = ["plot_fvc_distances"]


def _plot_wok_distance(data_F: polars.DataFrame, ax: Axes, is_dither: bool = False):
    colours = ["g", "r", "b"]
    for ii, fibre in enumerate(["Metrology", "APOGEE", "BOSS"]):
        data_fibre = data_F.filter(polars.col.fibre_type == fibre)

        if fibre != "Metrology" and is_dither is False:
            data_fibre = data_fibre.filter(polars.col.assigned, polars.col.on_target)
        if len(data_fibre) == 0:
            continue

        wok_distance = data_fibre["wok_distance"].to_numpy() * 1000.0
        wok_distance_bin = numpy.histogram(wok_distance, bins=numpy.arange(0, 105, 5))

        perc_90 = float(numpy.percentile(wok_distance, 90))
        ax.plot(
            wok_distance_bin[1][1:] - wok_distance_bin[1][1] / 2.0,  # type: ignore
            wok_distance_bin[0],
            linestyle="-",
            color=colours[ii],
            label=rf"{fibre}: {perc_90:.1f} $\mu$m",
            zorder=10,
        )

        if fibre == "Metrology":
            for perc_q in [90]:
                perc = float(numpy.percentile(wok_distance, perc_q))
                ax.axvline(x=perc, color="k", linestyle="--", linewidth=0.5, zorder=0)

        ax.set_xlim(0, 100)

        ax.set_xlabel("Wok distance [microns]")
        ax.set_ylabel("Number")

        ax.set_title("Wok distance")

        ax.legend()


def _plot_sky_distance(
    data_F: polars.DataFrame,
    ax: Axes,
    column: str,
    is_dither: bool = False,
    plot_metrology: bool = True,
    title: str = "Sky distance",
):
    colours = ["g", "r", "b"]
    for ii, fibre in enumerate(["Metrology", "APOGEE", "BOSS"]):
        if fibre == "Metrology" and not plot_metrology:
            continue

        data_fibre = data_F.filter(polars.col.fibre_type == fibre)

        if fibre != "Metrology" and is_dither is False:
            data_fibre = data_fibre.filter(polars.col.assigned, polars.col.on_target)
        if len(data_fibre) == 0:
            continue

        sky_distance = data_fibre[column].to_numpy()
        sky_distance_bin = numpy.histogram(sky_distance, bins=numpy.arange(0, 1.5, 0.1))

        perc_90 = float(numpy.percentile(sky_distance, 90))
        ax.plot(
            sky_distance_bin[1][1:] - sky_distance_bin[1][1] / 2.0,  # type: ignore
            sky_distance_bin[0],
            linestyle="-",
            color=colours[ii],
            label=f"{fibre}: {perc_90:.2f} arcsec",
            zorder=10,
        )

        if fibre == "Metrology":
            for perc_q in [90]:
                perc = float(numpy.percentile(sky_distance, perc_q))
                ax.axvline(x=perc, color="k", linestyle="--", linewidth=0.5, zorder=0)

        ax.set_xlim(0, 1.5)

        ax.set_xlabel("Sky distance [arcsec]")
        ax.set_ylabel("Number")

        ax.set_title(title)

        ax.legend()


def _plot_sky_quiver(data_F: polars.DataFrame, ax: Axes, is_dither: bool = False):
    colours = ["r", "b"]
    for ii, fibre in enumerate(["APOGEE", "BOSS"]):
        data_fibre = data_F.filter(polars.col.fibre_type == fibre)

        if is_dither is False:
            data_fibre = data_fibre.filter(polars.col.assigned, polars.col.on_target)

        if len(data_fibre) == 0:
            continue

        q = ax.quiver(
            data_fibre["ra_epoch"].to_numpy(),
            data_fibre["dec_epoch"].to_numpy(),
            data_fibre["ra_distance"].to_numpy(),
            data_fibre["dec_distance"].to_numpy(),
            color=colours[ii],
            label=fibre,
        )

        ax.quiverkey(
            q,
            X=0.05,
            Y=0.05 * (ii + 1),
            U=0.2,
            label="0.2 arcsec",
            labelpos="E",
        )

        ax.set_xlabel("Right Ascension")
        ax.set_ylabel("Declination")

        ax.set_title("confSummary(ra/dec) - confSummaryF(ra/dec)")

        ax.legend()


def plot_fvc_distances(
    configuration: BaseConfiguration,
    fibre_data_F: polars.DataFrame,
    path: str | pathlib.Path | None = None,
):
    """Plots several panels with FVC analysis and statistics.

    Parameters
    ----------
    configuration
        The `.Configuration` object associated with the FVC measurements.
    fibre_data_F
        Data frame with the measured values from the FVC.
    path
        The path where the plot will be saved. If ``None``, the plot it not
        saved or shown.

    Returns
    -------
    figure
        Matplotlib figure with the plots.

    """

    data = configuration.assignment.fibre_data.clone().sort("index")
    data_F = fibre_data_F.clone().sort("index")

    is_dither = configuration.is_dither

    data_F = data_F.with_columns(
        xwok_distance=data["xwok"] - data_F["xwok"],
        ywok_distance=data["ywok"] - data_F["ywok"],
    )

    wok_distance = numpy.hypot(data_F["xwok_distance"], data_F["ywok_distance"])
    data_F = data_F.with_columns(wok_distance=polars.Series(wok_distance))

    deccen: float = float(configuration.assignment.boresight[0][1])
    cos_dec = numpy.cos(numpy.deg2rad(float(deccen)))

    data_F = data_F.with_columns(
        ra_distance=(data["ra_epoch"] - data_F["ra_epoch"]) * cos_dec * 3600.0,
        dec_distance=(data["dec_epoch"] - data_F["dec_epoch"]) * 3600.0,
    )

    sky_distance = numpy.hypot(data_F["ra_distance"], data_F["dec_distance"])
    data_F = data_F.with_columns(sky_distance=polars.Series(sky_distance))

    if not is_dither:
        data_F = data_F.with_columns(
            racat_distance=(data["ra_icrs"] - data_F["ra_icrs"]) * cos_dec * 3600.0,
            deccat_distance=(data["dec_icrs"] - data_F["dec_icrs"]) * 3600.0,
        )

        skycat_distance = numpy.hypot(
            data_F["racat_distance"],
            data_F["deccat_distance"],
        )
        data_F = data_F.with_columns(skycat_distance=polars.Series(skycat_distance))

    with plt.ioff():  # type: ignore
        seaborn.set_theme()

        fig, axes = plt.subplots(1, 3, figsize=(30, 10))

        if not is_dither:
            data_F = data_F.group_by("positioner_id").map_groups(
                lambda g: g.filter(
                    polars.col.assigned.any(),
                    polars.col.on_target.any(),
                    polars.col.valid.all(),
                )
            )

        assert isinstance(axes, numpy.ndarray)

        _plot_wok_distance(data_F, axes[0])

        _plot_sky_distance(
            data_F,
            axes[1],
            "sky_distance",
            is_dither=is_dither,
            plot_metrology=True,
            title="Sky distance (ra/dec vs ra/dec)",
        )

        # if not is_dither:
        #     _plot_sky_distance(
        #         data_F,
        #         axes[1, 0],
        #         "skycat_distance",
        #         is_dither=is_dither,
        #         plot_metrology=False,
        #         title="Sky distance (ra/dec vs racat/deccat)",
        #     )

        _plot_sky_quiver(data_F, axes[2], is_dither=is_dither)

        fig.suptitle(
            f"Configuration ID: {configuration.configuration_id}"
            + (" (dithered)" if is_dither else "")
        )

        plt.tight_layout()

        if path:
            fig.savefig(str(path))
            plt.close(fig)

        seaborn.reset_defaults()

    return fig
