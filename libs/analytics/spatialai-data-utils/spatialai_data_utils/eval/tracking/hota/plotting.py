# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT AND Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Portions of this file are adapted from TrackEval:
# https://github.com/kovalp/TrackEval/tree/1.3.0
#
# MIT License
#
# Copyright (c) 2020 Jonathon Luiten
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import logging
import numpy as np
from spatialai_data_utils.eval.tracking.hota.utils import TrackEvalException


def plot_compare_trackers(tracker_folder, tracker_list, cls, output_folder, plots_list=None):
    """
    Create plots which compare metrics across different trackers

    :param str tracker_folder: root tracker folder
    :param str tracker_list: names of all trackers
    :param List[cls] cls: names of classes
    :param str output_folder: root folder to save the plots in
    :param List[str] plots_list: list of all plots to generate
    :return: None
    ::

        plotting.plot_compare_trackers(tracker_folder, tracker_list, cls, output_folder, plots_list)
    """
    if plots_list is None:
        plots_list = get_default_plots_list()

    # Load data
    data = load_multiple_tracker_summaries(tracker_folder, tracker_list, cls)
    out_loc = os.path.join(output_folder, cls)

    # Plot
    print("\n")
    for args in plots_list:
        create_comparison_plot(data, out_loc, *args)


def get_default_plots_list():
    """
    Create a intermediate config to define the type of plots.
    The plot uses the following order to generate the charts:
    y_label, x_label, sort_label, bg_label, bg_function

    :param None
    :return: List[List[str]] plots_list: detailed description of the plots 
    ::

        plotting.get_default_plots_list(tracker_folder, tracker_list, cls, output_folder, plots_list)
    """
    plots_list = [
        ['AssA', 'DetA', 'HOTA', 'HOTA', 'geometric_mean'],
        ['AssPr', 'AssRe', 'HOTA', 'AssA', 'jaccard'],
        ['DetPr', 'DetRe', 'HOTA', 'DetA', 'jaccard'],
        ['HOTA(0)', 'LocA(0)', 'HOTA', 'HOTALocA(0)', 'multiplication'],
        ['HOTA', 'LocA', 'HOTA', None, None],

        ['HOTA', 'MOTA', 'HOTA', None, None],
        ['HOTA', 'IDF1', 'HOTA', None, None],
        ['IDF1', 'MOTA', 'HOTA', None, None],
    ]
    return plots_list


def load_multiple_tracker_summaries(tracker_folder, tracker_list, cls):
    """
    Loads summary data for multiple trackers 

    :param str tracker_folder: directory of the tracker folder
    :param str tracker_list: names of the trackers
    :param str cls: names of all classes

    :return: Dict[str] data: summaried data of the trackers 
    ::

        plotting.load_multiple_tracker_summaries(tracker_folder, tracker_list, cls, output_folder, plots_list)
    """
    data = {}
    for tracker in tracker_list:
        with open(os.path.join(tracker_folder, tracker, cls + '_summary.txt')) as f:
            keys = next(f).split(' ')
            done = False
            while not done:
                values = next(f).split(' ')
                if len(values) == len(keys):
                    done = True
            data[tracker] = dict(zip(keys, map(float, values)))
    return data


def create_comparison_plot(data, out_loc, y_label, x_label, sort_label, bg_label=None, bg_function=None, settings=None):
    """ 
    Creates a scatter plot comparing multiple trackers between two metric fields, with one on the x-axis and the
    other on the y axis. Adds pareto optical lines and (optionally) a background contour.

    :param data: dict of dicts such that data[tracker_name][metric_field_name] = float
    :param str y_label: the metric_field_name to be plotted on the y-axis
    :param strx_label: the metric_field_name to be plotted on the x-axis
    :param str sort_label: the metric_field_name by which trackers are ordered and ranked
    :param str bg_label: the metric_field_name by which (optional) background contours are plotted
    :param str bg_function: the (optional) function bg_function(x,y) which converts the x_label / y_label values into bg_label.
    :param Dict[str] settings: dict of plot settings with keys:
        'gap_val': gap between axis ticks and bg curves.
        'num_to_plot': maximum number of trackers to plot

    :return: None
    ::

        plotting.create_comparison_plot(x_values, y_values)
    """

    # Only loaded when run to reduce minimum requirements
    from matplotlib import pyplot as plt

    # Get plot settings
    if settings is None:
        gap_val = 2
        num_to_plot = 20
    else:
        gap_val = settings['gap_val']
        num_to_plot = settings['num_to_plot']

    if (bg_label is None) != (bg_function is None):
        raise TrackEvalException('bg_function and bg_label must either be both given or neither given.')

    # Extract data
    tracker_names = np.array(list(data.keys()))
    sort_index = np.array([data[t][sort_label] for t in tracker_names]).argsort()[::-1]
    x_values = np.array([data[t][x_label] for t in tracker_names])[sort_index][:num_to_plot]
    y_values = np.array([data[t][y_label] for t in tracker_names])[sort_index][:num_to_plot]

    # Print info on what is being plotted
    tracker_names = tracker_names[sort_index][:num_to_plot]
    logging.info('Plotting %s vs %s...' % (y_label, x_label))
    #for i, name in enumerate(tracker_names):
        #print('%i: %s' % (i+1, name))

    # Find best fitting boundaries for data
    boundaries = _get_boundaries(x_values, y_values, round_val=gap_val/2)

    fig = plt.figure()

    # Plot background contour
    if bg_function is not None:
        _plot_bg_contour(bg_function, boundaries, gap_val)

    # Plot pareto optimal lines
    _plot_pareto_optimal_lines(x_values, y_values)

    # Plot data points with number labels
    labels = np.arange(len(y_values)) + 1
    plt.plot(x_values, y_values, 'b.', markersize=15)
    for xx, yy, l in zip(x_values, y_values, labels):
        plt.text(xx, yy, str(l), color="red", fontsize=15)

    # Add extra explanatory text to plots
    plt.text(0, -0.11, 'label order:\nHOTA', horizontalalignment='left', verticalalignment='center',
             transform=fig.axes[0].transAxes, color="red", fontsize=12)
    if bg_label is not None:
        plt.text(1, -0.11, 'curve values:\n' + bg_label, horizontalalignment='right', verticalalignment='center',
                 transform=fig.axes[0].transAxes, color="grey", fontsize=12)

    plt.xlabel(x_label, fontsize=15)
    plt.ylabel(y_label, fontsize=15)
    title = y_label + ' vs ' + x_label
    if bg_label is not None:
        title += ' (' + bg_label + ')'
    plt.title(title, fontsize=17)
    plt.xticks(np.arange(0, 100, gap_val))
    plt.yticks(np.arange(0, 100, gap_val))
    min_x, max_x, min_y, max_y = boundaries
    plt.xlim(min_x, max_x)
    plt.ylim(min_y, max_y)
    plt.gca().set_aspect('equal', adjustable='box')
    plt.tight_layout()

    os.makedirs(out_loc, exist_ok=True)
    filename = os.path.join(out_loc, title.replace(' ', '_'))
    plt.savefig(filename + '.pdf', bbox_inches='tight', pad_inches=0.05)
    plt.savefig(filename + '.png', bbox_inches='tight', pad_inches=0.05)


def _get_boundaries(x_values, y_values, round_val):
    """
    Computes boundaries of a plot

    :param List[Float] x_values: x values
    :param List[Float] y_values: y values
    :param Float round_val: interval

    :return: Float, Float, Float, Float: boundaries of the plot
    ::

        plotting._get_boundaries(x_values, y_values)
    """
    x1 = np.min(np.floor((x_values - 0.5) / round_val) * round_val)
    x2 = np.max(np.ceil((x_values + 0.5) / round_val) * round_val)
    y1 = np.min(np.floor((y_values - 0.5) / round_val) * round_val)
    y2 = np.max(np.ceil((y_values + 0.5) / round_val) * round_val)
    x_range = x2 - x1
    y_range = y2 - y1
    max_range = max(x_range, y_range)
    x_center = (x1 + x2) / 2
    y_center = (y1 + y2) / 2
    min_x = max(x_center - max_range / 2, 0)
    max_x = min(x_center + max_range / 2, 100)
    min_y = max(y_center - max_range / 2, 0)
    max_y = min(y_center + max_range / 2, 100)
    return min_x, max_x, min_y, max_y


def geometric_mean(x, y):
    """
    Computes geometric mean

    :param Float x: x values
    :param Float y: y values

    :return: Float: geometric mean value 
    ::

        plotting.geometric_mean(x_values, y_values)
    """
    return np.sqrt(x * y)


def jaccard(x, y):
    x = x / 100
    y = y / 100
    return 100 * (x * y) / (x + y - x * y)


def multiplication(x, y):
    """
    Computes multiplication for plots

    :param Float x: x values
    :param Float y: y values

    :return: Float: multiplied value 
    ::

        plotting.multiplication(x_values, y_values)
    """
    return x * y / 100


bg_function_dict = {
    "geometric_mean": geometric_mean,
    "jaccard": jaccard,
    "multiplication": multiplication,
    }


def _plot_bg_contour(bg_function, plot_boundaries, gap_val):
    """
    Plot background contour

    :param Dict[str:func()] bg_function: sort order function
    :param List[float] plot_boundaries: limit values for the plot
    :param int gap_val: interval value

    :return: None 
    ::

        plotting._plot_bg_contour(x_values, y_values)
    """
    # Only loaded when run to reduce minimum requirements
    from matplotlib import pyplot as plt

    # Plot background contour
    min_x, max_x, min_y, max_y = plot_boundaries
    x = np.arange(min_x, max_x, 0.1)
    y = np.arange(min_y, max_y, 0.1)
    x_grid, y_grid = np.meshgrid(x, y)
    if bg_function in bg_function_dict.keys():
        z_grid = bg_function_dict[bg_function](x_grid, y_grid)
    else:
        raise TrackEvalException("background plotting function '%s' is not defined." % bg_function)
    levels = np.arange(0, 100, gap_val)
    con = plt.contour(x_grid, y_grid, z_grid, levels, colors='grey')

    def bg_format(val):
        s = '{:1f}'.format(val)
        return '{:.0f}'.format(val) if s[-1] == '0' else s

    con.levels = [bg_format(val) for val in con.levels]
    plt.clabel(con, con.levels, inline=True, fmt='%r', fontsize=8)


def _plot_pareto_optimal_lines(x_values, y_values):
    """
    Plot pareto optimal lines

    :param List[float] x_values: values to plot on x axis
    :param List[float] y_values: values to plot on y axis

    :return: None 
    ::

        plotting._plot_pareto_optimal_lines(x_values, y_values)
    """

    # Only loaded when run to reduce minimum requirements
    from matplotlib import pyplot as plt

    # Plot pareto optimal lines
    cxs = x_values
    cys = y_values
    best_y = np.argmax(cys)
    x_pareto = [0, cxs[best_y]]
    y_pareto = [cys[best_y], cys[best_y]]
    t = 2
    remaining = cxs > x_pareto[t - 1]
    cys = cys[remaining]
    cxs = cxs[remaining]
    while len(cxs) > 0 and len(cys) > 0:
        best_y = np.argmax(cys)
        x_pareto += [x_pareto[t - 1], cxs[best_y]]
        y_pareto += [cys[best_y], cys[best_y]]
        t += 2
        remaining = cxs > x_pareto[t - 1]
        cys = cys[remaining]
        cxs = cxs[remaining]
    x_pareto.append(x_pareto[t - 1])
    y_pareto.append(0)
    plt.plot(np.array(x_pareto), np.array(y_pareto), '--r')
