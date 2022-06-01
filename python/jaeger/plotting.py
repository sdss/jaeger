#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2022-05-04
# @Filename: plotting.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import pathlib

import matplotlib.pyplot as plt
import numpy
import pandas
import seaborn

from jaeger.target.configuration import BaseConfiguration


__all__ = ["plot_fvc_distances"]


def _plot_wok_distance(data_F: pandas.DataFrame, ax: plt.Axes, is_dither: bool = False):

    colours = ["g", "r", "b"]
    for ii, fibre in enumerate(["Metrology", "APOGEE", "BOSS"]):

        data_fibre = data_F.loc[pandas.IndexSlice[:, fibre], :].copy()

        if fibre != "Metrology" and is_dither is False:
            data_fibre = data_fibre.loc[
                (data_fibre.assigned == 1) & (data_fibre.on_target == 1), :
            ]
        if len(data_fibre) == 0:
            continue

        wok_distance = data_fibre.wok_distance * 1000.0
        wok_distance_bin = numpy.histogram(wok_distance, bins=numpy.arange(0, 105, 5))

        perc_90 = numpy.percentile(wok_distance, 90)
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
                perc = numpy.percentile(wok_distance, perc_q)
                ax.axvline(x=perc, color="k", linestyle="--", linewidth=0.5, zorder=0)

        ax.set_xlim(0, 100)

        ax.set_xlabel("Wok distance [microns]")
        ax.set_ylabel("Number")

        ax.set_title("Wok distance")

        ax.legend()


def _plot_sky_distance(
    data_F: pandas.DataFrame,
    ax: plt.Axes,
    column: str,
    is_dither: bool = False,
    plot_metrology: bool = True,
    title: str = "Sky distance",
):

    colours = ["g", "r", "b"]
    for ii, fibre in enumerate(["Metrology", "APOGEE", "BOSS"]):
        if fibre == "Metrology" and not plot_metrology:
            continue

        data_fibre = data_F.loc[pandas.IndexSlice[:, fibre], :].copy()

        if fibre != "Metrology" and is_dither is False:
            data_fibre = data_fibre.loc[
                (data_fibre.assigned == 1) & (data_fibre.on_target == 1), :
            ]
        if len(data_fibre) == 0:
            continue

        sky_distance = data_fibre[column]
        sky_distance_bin = numpy.histogram(sky_distance, bins=numpy.arange(0, 1.5, 0.1))

        perc_90 = numpy.percentile(sky_distance, 90)
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
                perc = numpy.percentile(sky_distance, perc_q)
                ax.axvline(x=perc, color="k", linestyle="--", linewidth=0.5, zorder=0)

        ax.set_xlim(0, 1.5)

        ax.set_xlabel("Sky distance [arcsec]")
        ax.set_ylabel("Number")

        ax.set_title(title)

        ax.legend()


def _plot_sky_quiver(data_F: pandas.DataFrame, ax: plt.Axes, is_dither: bool = False):

    colours = ["r", "b"]
    for ii, fibre in enumerate(["APOGEE", "BOSS"]):

        data_fibre = data_F.loc[pandas.IndexSlice[:, fibre], :].copy()

        if is_dither is False:
            data_fibre = data_fibre.loc[
                (data_fibre.assigned == 1) & (data_fibre.on_target == 1), :
            ]
        if len(data_fibre) == 0:
            continue

        q = ax.quiver(
            data_fibre.ra_epoch,
            data_fibre.dec_epoch,
            data_fibre.ra_distance,
            data_fibre.dec_distance,
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
    fibre_data_F: pandas.DataFrame,
    path: str | pathlib.Path | None = None,
):
    """Plots several panels with FVC analysis and statistics.

    Parameters
    ----------
    fibre_data
        Data frame with the targetting data.
    fibre_data_F
        Data frame with the measured values from the FVC.

    Returns
    -------
    figure
        Matplotlib figure with the plots.

    """

    data = configuration.assignment_data.fibre_table.copy()
    data = data.reset_index().set_index(["positioner_id", "fibre_type"])

    data_F = fibre_data_F.copy()
    data_F = data_F.reset_index().set_index(["positioner_id", "fibre_type"])

    is_dither = configuration.is_dither

    data = data.loc[data_F.index, :]

    data_F["xwok_distance"] = data.xwok - data_F.xwok
    data_F["ywok_distance"] = data.ywok - data_F.ywok
    data_F["wok_distance"] = numpy.hypot(data_F.xwok_distance, data_F.ywok_distance)

    deccen = configuration.assignment_data.boresight[0][1]
    cos_dec = numpy.cos(numpy.deg2rad(float(deccen)))

    data_F["ra_distance"] = (data.ra_epoch - data_F.ra_epoch) * cos_dec * 3600.0
    data_F["dec_distance"] = (data.dec_epoch - data_F.dec_epoch) * 3600.0
    data_F["sky_distance"] = numpy.hypot(data_F.ra_distance, data_F.dec_distance)

    if not is_dither:
        data_F["racat_distance"] = (data_F.ra_icrs - data_F.ra_epoch) * cos_dec * 3600.0
        data_F["deccat_distance"] = (data_F.dec_icrs - data_F.dec_epoch) * 3600.0
        data_F["skycat_distance"] = numpy.hypot(
            data_F.racat_distance, data_F.deccat_distance
        )

    with plt.ioff():  # type: ignore

        seaborn.set_theme()

        fig, axes = plt.subplots(1, 3, figsize=(30, 10))

        if not is_dither:
            data_F = data_F.groupby("positioner_id").filter(
                lambda g: g.assigned.any() & g.on_target.any() & g.valid.all()
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
