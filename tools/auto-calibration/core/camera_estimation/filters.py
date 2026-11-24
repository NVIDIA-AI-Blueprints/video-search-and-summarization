# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
import plotly.express as px
import plotly.graph_objects as go

# my packages
import conf 
from preprocess_bbox import BboxConfig

def func(xy, a, b, c, d, e, f):
    """ Function for curve fitting """
    x, y = xy
    return a + b*x + c*y + d*x**2 + e*y**2 + f*x*y

def nonlinear_regression(X, y, target):
    """ Nonlinear regression for curve fitting 
    
    Args:
        X(ndarray): x values
        y(ndarray): y values
        target(str): 'aspect_ratio', 'height', 'width'
    
    Returns:
        popt(ndarray): optimized parameters
    """
    if target == 'aspect_ratio':
        low_bound = conf.AR_FILTER_LOW_BOUND
        high_bound = conf.AR_FILTER_HIGH_BOUND
        popt, pcov = curve_fit(func, (X[:,0], X[:,1]), y, bounds=(low_bound, high_bound)) 
    elif target == 'height':
        low_bound = conf.HEIGHT_FILTER_LOW_BOUND
        high_bound = conf.HEIGHT_FILTER_HIGH_BOUND
        popt, pcov = curve_fit(func, (X[:,0], X[:,1]), y, bounds=(low_bound, high_bound)) 
    elif target == 'width':
        low_bound = conf.WIDTH_FILTER_LOW_BOUND
        high_bound = conf.WIDTH_FILTER_HIGH_BOUND 
        popt, pcov = curve_fit(func, (X[:,0], X[:,1]), y, bounds=(low_bound, high_bound)) 
    else:
        raise ValueError('Unknown target')
    
    df = pd.DataFrame()
    df['cx'] = X[:,0]
    df['cy'] = X[:,1]
    df['z'] = y
    return popt


def filter_aspect_ratio(df, bb_conf: BboxConfig, out_name=None):
    """ Filter bboxes with aspect ratio
    
    Args:
        df(pd.DataFrame): the input DataFrame
        bb_conf(BboxConfig): the configuration for bounding box sampling
        out_name(str): the output file name for drawing the filtered samples

    Returns:
        df_filtered(pd.DataFrame): the filtered DataFrame
    """
    df = df[df['aspect_ratio'] < 1.0] # filter out aspect ratio > 1.0
    cx = df['cx'].to_numpy()
    cy = df['cy'].to_numpy()
    aspect_ratio = df['aspect_ratio'].to_numpy()

    # filtering with aspect ratio
    X = np.concatenate((cx.reshape(-1,1), cy.reshape(-1,1)), axis=1)
    aratio_popt = nonlinear_regression(X, aspect_ratio, 'aspect_ratio') 

    # aspect ratio filter
    aratio_residual = []
    for idx, row in df.iterrows():
        cx = row['cx']
        cy = row['cy']
        aratio_pred = func((cx, cy), *aratio_popt)
        aratio_residual.append(abs(aratio_pred - row['aspect_ratio']))
    df['ratio_residual'] = aratio_residual

    # filter out
    df_filtered = df[df['ratio_residual'] < bb_conf.sampling_ar_residual] 
    print('#samples(aspect ratio cutoff): ', len(df_filtered))

    # draw the filtered samples
    fig = px.scatter_3d(df, x='cx', y='cy', z='aspect_ratio', opacity=0.7) 
    fig.update_traces(name='before filtering', 
                      marker=dict(size=3, line=dict(width=2, color='blue')),
                      selector=dict(mode='markers'))
    trace1 = go.Scatter3d(
        name='After filtering',
        x=df_filtered['cx'],
        y=df_filtered['cy'],
        z=df_filtered['aspect_ratio'],
        marker=dict(size=3, line=dict(width=2, color='red')),
        mode='markers'
        )
    fig.add_trace(trace1)
 
    # Visualize the fitting curve
    x_surf, y_surf = np.meshgrid(np.linspace(df_filtered.cx.min(), 
                                             df_filtered.cx.max()),
                                             np.linspace(df_filtered.cy.min(), 
                                                         df_filtered.cy.max()))
    z_surf = func((x_surf, y_surf), *aratio_popt)
    light_yellow = [[0, '#FFDB58'], [1, '#FFDB58']]
    fig.add_trace(go.Surface(name='fitting curve', 
                             x=x_surf, y=y_surf, z=z_surf, 
                             colorscale=light_yellow,  showscale=False)) 
    fig.write_image(f'{out_name}')
    if conf.DEBUG:
        fig.show()
    return df_filtered

def filter_width(df, bb_conf: BboxConfig, out_name=None):
    """ Filter bboxes with width
    
    Args:
        df(pd.DataFrame): the input DataFrame
        bb_conf(BboxConfig): the configuration for bounding box sampling
        out_name(str): the output file name for drawing the filtered samples

    Returns:
        df_filtered(pd.DataFrame): the filtered DataFrame
    """
    cx = df['cx'].to_numpy()
    cy = df['cy'].to_numpy()
    width = df['width'].to_numpy()

    # filtering with width
    X = np.concatenate((cx.reshape(-1,1), cy.reshape(-1,1)), axis=1)
    width_popt = nonlinear_regression(X, width, 'width') 

    # width filter
    width_residual = []
    for idx, row in df.iterrows():
        cx = row['cx']
        cy = row['cy']
        width_pred = func((cx, cy), *width_popt)
        width_residual.append(abs(width_pred - row['width']))
    df['width_residual'] = width_residual

    # filter out
    df_filtered = df[df['width_residual'] < bb_conf.sampling_width_residual] 
    print('#samples(width cutoff): ', len(df_filtered))

    # draw the filtered samples
    fig = px.scatter_3d(df, x='cx', y='cy', z='width', opacity=0.7) 
    fig.update_traces(marker=dict(size=3, 
                                  line=dict(width=2,
                                            color='blue')),
                    selector=dict(mode='markers'))
    trace1 = go.Scatter3d(
        name='After filtering',
        x=df_filtered['cx'],
        y=df_filtered['cy'],
        z=df_filtered['width'],
        marker=dict(size=3, line=dict(width=2, color='red')),
        mode='markers'
        )
    fig.add_trace(trace1)
    # Visualize the fitting curve
    x_surf, y_surf = np.meshgrid(np.linspace(df_filtered.cx.min(), 
                                             df_filtered.cx.max()),
                                             np.linspace(df_filtered.cy.min(), 
                                                         df_filtered.cy.max()))
    z_surf = func((x_surf, y_surf), *width_popt)

    light_yellow = [[0, '#FFDB58'], [1, '#FFDB58']]
    fig.add_trace(go.Surface(x=x_surf, y=y_surf, z=z_surf, 
                             colorscale=light_yellow,  showscale=False)) 
    fig.write_image(f'{out_name}')
    if conf.DEBUG:
        fig.show()
    return df_filtered, width_popt

def filter_height(df, bb_conf: BboxConfig, out_name=None):
    """ Filter bboxes with height 
    
    Args:
        df(pd.DataFrame): the input DataFrame
        bb_conf(BboxConfig): the configuration for bounding box sampling
        out_name(str): the output file name for drawing the filtered samples

    Returns:
        df_filtered(pd.DataFrame): the filtered DataFrame
    """
    cx = df['cx'].to_numpy()
    cy = df['cy'].to_numpy()
    height = df['height'].to_numpy()

    # filtering with height 
    X = np.concatenate((cx.reshape(-1,1), cy.reshape(-1,1)), axis=1)
    height_popt = nonlinear_regression(X, height, 'height') 
    print('height_popt', height_popt)

    # height filter
    height_residual = []
    for idx, row in df.iterrows():
        cx = row['cx']
        cy = row['cy']
        height_pred = func((cx, cy), *height_popt)
        height_residual.append(height_pred - row['height'])

    df['height_residual'] = height_residual

    # filter out
    df_filtered = df[df['height_residual'] < bb_conf.sampling_height_residual]
    print('#samples(height cutoff): ', len(df_filtered))

    # draw the filtered samples
    fig = px.scatter_3d(df, x='cx', y='cy', z='height', opacity=0.7)
    fig.update_traces(name='before filtering', 
                      marker=dict(size=3, line=dict(width=2, color='blue')),
                    selector=dict(mode='markers'))
    trace1 = go.Scatter3d(
        name='After filtering',
        x=df_filtered['cx'],
        y=df_filtered['cy'],
        z=df_filtered['height'],
        marker=dict(size=3, line=dict(width=2, color='red')),
        mode='markers'
        )
    fig.add_trace(trace1)

    # Visualize the fitting curve
    x_surf, y_surf = np.meshgrid(np.linspace(df_filtered.cx.min(), 
                                             df_filtered.cx.max()),
                                             np.linspace(df_filtered.cy.min(), 
                                                         df_filtered.cy.max()))
    z_surf = func((x_surf, y_surf), *height_popt)

    light_yellow = [[0, '#FFDB58'], [1, '#FFDB58']]
    fig.add_trace(go.Surface(x=x_surf, y=y_surf, z=z_surf, 
                             colorscale=light_yellow,  showscale=False)) 

    #if out_name is None:
    print('out_name: ', out_name)
    fig.write_image(f'{out_name}')
    if conf.DEBUG:
        fig.show()

    return df_filtered, height_popt
