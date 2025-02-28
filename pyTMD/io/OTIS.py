#!/usr/bin/env python
u"""
OTIS.py
Written by Tyler Sutterley (12/2024)

Reads files for a tidal model and makes initial calculations to run tide program
Includes functions to extract tidal harmonic constants from OTIS tide models for
    given locations

Reads OTIS format tidal solutions provided by Oregon State University and ESR
    http://volkov.oce.orst.edu/tides/region.html
    https://www.esr.org/research/polar-tide-models/list-of-polar-tide-models/
    ftp://ftp.esr.org/pub/datasets/tmd/

INPUTS:
    ilon: longitude to interpolate
    ilat: latitude to interpolate
    grid_file: grid file for model
    model_file: model file containing each constituent

OPTIONS:
    type: tidal variable to run
        z: heights
        u: horizontal transport velocities
        U: horizontal depth-averaged transport
        v: vertical transport velocities
        V: vertical depth-averaged transport
    method: interpolation method
        bilinear: quick bilinear interpolation
        spline: scipy bivariate spline interpolation
        linear, nearest: scipy regular grid interpolations
    extrapolate: extrapolate model using nearest-neighbors
    cutoff: extrapolation cutoff in kilometers
        set to np.inf to extrapolate for all points
    grid: binary file type to read
        ATLAS: reading a global solution with localized solutions
        TMD3: combined global or local netCDF4 solution
        OTIS: combined global or local solution
    apply_flexure: apply ice flexure scaling factor to constituents

OUTPUTS:
    amplitude: amplitudes of tidal constituents
    phase: phases of tidal constituents
    D: bathymetry of tide model
    constituents: list of model constituents

PYTHON DEPENDENCIES:
    numpy: Scientific Computing Tools For Python
        https://numpy.org
        https://numpy.org/doc/stable/user/numpy-for-matlab-users.html
    scipy: Scientific Tools for Python
        https://docs.scipy.org/doc/
    netCDF4: Python interface to the netCDF C library
        https://unidata.github.io/netcdf4-python/netCDF4/index.html

PROGRAM DEPENDENCIES:
    crs.py: Coordinate Reference System (CRS) routines
    interpolate.py: interpolation routines for spatial data

UPDATE HISTORY:
    Updated 12/2024: released version of TMD3 has different variable names
    Updated 11/2024: expose buffer distance for cropping tide model data
    Updated 10/2024: save latitude and longitude to output constituent object
        fix error when using default bounds in extract_constants
    Updated 09/2024: using new JSON dictionary format for model projections
    Updated 08/2024: revert change and assume crop bounds are projected
    Updated 07/2024: added crop and bounds keywords for trimming model data
        convert the crs of bounds when cropping model data
    Updated 06/2024: change int32 to int to prevent overflows with numpy 2.0
    Updated 02/2024: don't overwrite hu and hv in _interpolate_zeta
        changed variable for setting global grid flag to is_global
    Updated 01/2024: construct currents masks differently if not global
        renamed currents masks and bathymetry interpolation functions
    Updated 12/2023: use new crs class for coordinate reprojection
    Updated 10/2023: fix transport variable entry for TMD3 models
    Updated 09/2023: prevent overwriting ATLAS compact x and y coordinates
    Updated 08/2023: changed ESR netCDF4 format to TMD3 format
    Updated 04/2023: using pathlib to define and expand tide model paths
    Updated 03/2023: add basic variable typing to function inputs
        new function name for converting coordinate reference systems
    Updated 12/2022: refactor tide read programs under io
        new functions to read and interpolate from constituents class
        refactored interpolation routines into new module
    Updated 11/2022: place some imports within try/except statements
        fix variable reads for ATLAS compact data formats
        use f-strings for formatting verbose or ascii output
    Updated 10/2022: invert current tide masks to be True for invalid points
    Updated 06/2022: unit updates in the ESR netCDF4 format
    Updated 05/2022: add functions for using ESR netCDF4 format models
        changed keyword arguments to camel case
    Updated 04/2022: updated docstrings to numpy documentation format
        use longcomplex data format to be windows compliant
    Updated 03/2022: invert tide mask to be True for invalid points
        add separate function for resampling ATLAS compact global model
        decode ATLAS compact constituents for Python3 compatibility
        reduce iterative steps when combining ATLAS local models
    Updated 02/2022: use ceiling of masks for interpolation
    Updated 07/2021: added checks that tide model files are accessible
    Updated 06/2021: fix tidal currents for bilinear interpolation
        check for nan points when reading elevation and transport files
    Updated 05/2021: added option for extrapolation cutoff in kilometers
    Updated 03/2021: add extrapolation check where there are no invalid points
        prevent ComplexWarning for fill values when calculating amplitudes
        can read from single constituent TPXO9 ATLAS binary files
        replaced numpy bool/int to prevent deprecation warnings
    Updated 02/2021: set invalid values to nan in extrapolation
        replaced numpy bool to prevent deprecation warning
    Updated 12/2020: added valid data extrapolation with nearest_extrap
    Updated 09/2020: set bounds error to false for regular grid interpolations
        adjust dimensions of input coordinates to be iterable
        use masked arrays with atlas models and grids. make 2' grid with nearest
    Updated 08/2020: check that interpolated points are within range of model
        replaced griddata interpolation with scipy regular grid interpolators
    Updated 07/2020: added function docstrings. separate bilinear interpolation
        update griddata interpolation. changed type variable to keyword argument
    Updated 06/2020: output currents as numpy masked arrays
        use argmin and argmax in bilinear interpolation
    Updated 11/2019: interpolate heights and fluxes to numpy masked arrays
    Updated 09/2019: output as numpy masked arrays instead of nan-filled arrays
    Updated 01/2019: decode constituents for Python3 compatibility
    Updated 08/2018: added option grid for using ATLAS outputs that
        combine both global and localized tidal solutions
        added multivariate spline interpolation option
    Updated 07/2018: added different interpolation methods
    Updated 09/2017: Adapted for Python
"""
from __future__ import division, annotations

import copy
import struct
import logging
import pathlib
import numpy as np
import scipy.interpolate
import pyTMD.crs
import pyTMD.interpolate
import pyTMD.io.constituents
from pyTMD.utilities import import_dependency

# attempt imports
netCDF4 = import_dependency('netCDF4')

__all__ = [
    "extract_constants",
    "read_constants",
    "interpolate_constants",
    "read_otis_grid",
    "read_atlas_grid",
    "read_netcdf_grid",
    "read_constituents",
    "read_otis_elevation",
    "read_atlas_elevation",
    "read_otis_transport",
    "read_atlas_transport",
    "create_atlas_mask",
    "interpolate_atlas_model",
    "combine_atlas_model",
    "read_netcdf_file",
    "output_otis_grid",
    "output_otis_elevation",
    "output_otis_transport",
    "_extend_array",
    "_extend_matrix",
    "_crop",
    "_shift",
    "_mask_nodes",
    "_interpolate_mask",
    "_interpolate_zeta"
]

# PURPOSE: extract harmonic constants from tide models at coordinates
def extract_constants(
        ilon: np.ndarray,
        ilat: np.ndarray,
        grid_file: str | pathlib.Path | None = None,
        model_file: str | pathlib.Path | list | None = None,
        projection: dict | str | int | None = None,
        **kwargs
    ):
    """
    Reads files from tide models in OTIS and ATLAS-compact formats

    Makes initial calculations to run the tide program

    Spatially interpolates tidal constituents to input coordinates

    Parameters
    ----------
    ilon: np.ndarray
        longitude to interpolate
    ilat: np.ndarray
        latitude to interpolate
    grid_file: str, pathlib.Path or NoneType, default None
        grid file for model
    model_file: str, pathlib.Path, list or NoneType, default None
        model file containing each constituent
    projection: str or NoneType, default None,
        projection of tide model data
    type: str, default 'z'
        Tidal variable to read

            - ``'z'``: heights
            - ``'u'``: horizontal transport velocities
            - ``'U'``: horizontal depth-averaged transport
            - ``'v'``: vertical transport velocities
            - ``'V'``: vertical depth-averaged transport
    grid: str, default 'OTIS'
        Tide model file type to read

            - ``'ATLAS'``: reading a global solution with localized solutions
            - ``'OTIS'``: combined global or local solution
            - ``'TMD3'``: combined global or local netCDF4 solution
    crop: bool, default False
        Crop tide model data to (buffered) bounds
    bounds: list or NoneType, default None
        Boundaries for cropping tide model data
    buffer: int, float or NoneType, default None
        Buffer angle or distance for cropping tide model data
    method: str, default 'spline'
        Interpolation method

            - ``'bilinear'``: quick bilinear interpolation
            - ``'spline'``: scipy bivariate spline interpolation
            - ``'linear'``, ``'nearest'``: scipy regular grid interpolations
    extrapolate: bool, default False
        Extrapolate model using nearest-neighbors
    cutoff: float, default 10.0
        Extrapolation cutoff in kilometers

        Set to ``np.inf`` to extrapolate for all points
    apply_flexure: bool, default False
        Apply ice flexure scaling factor to height values

    Returns
    -------
    amplitude: np.ndarray
        amplitudes of tidal constituents
    phase: np.ndarray
        phases of tidal constituents
    D: np.ndarray
        bathymetry of tide model
    constituents: list
        list of model constituents
    """
    # set default keyword arguments
    kwargs.setdefault('type', 'z')
    kwargs.setdefault('grid', 'OTIS')
    kwargs.setdefault('crop', False)
    kwargs.setdefault('bounds', None)
    kwargs.setdefault('buffer', None)
    kwargs.setdefault('method', 'spline')
    kwargs.setdefault('extrapolate', False)
    kwargs.setdefault('cutoff', 10.0)
    kwargs.setdefault('apply_flexure', False)
    # raise warnings for deprecated keyword arguments
    deprecated_keywords = dict(TYPE='type',METHOD='method',
        EXTRAPOLATE='extrapolate',CUTOFF='cutoff',GRID='grid')
    for old,new in deprecated_keywords.items():
        if old in kwargs.keys():
            logging.warning(f"""Deprecated keyword argument {old}.
                Changed to '{new}'""")
            # set renamed argument to not break workflows
            kwargs[new] = copy.copy(kwargs[old])

    # check that grid file is accessible
    grid_file = pathlib.Path(grid_file).expanduser()
    if not grid_file.exists():
        raise FileNotFoundError(str(grid_file))

    # read the OTIS-format tide grid file
    if (kwargs['grid'] == 'ATLAS'):
        # if reading a global solution with localized solutions
        x0,y0,hz0,mz0,iob,dt,pmask,local = read_atlas_grid(grid_file)
        xi,yi,hz = combine_atlas_model(x0,y0,hz0,pmask,local,variable='depth')
        mz = create_atlas_mask(x0,y0,mz0,local,variable='depth')
    elif (kwargs['grid'] == 'TMD3'):
        # if reading a single TMD3 netCDF4 solution
        xi,yi,hz,mz,sf = read_netcdf_grid(grid_file)
    else:
        # if reading a single OTIS solution
        xi,yi,hz,mz,iob,dt = read_otis_grid(grid_file)
    # invert tide mask to be True for invalid points
    mz = np.logical_not(mz).astype(mz.dtype)

    # adjust dimensions of input coordinates to be iterable
    ilon = np.atleast_1d(np.copy(ilon))
    ilat = np.atleast_1d(np.copy(ilat))
    # run wrapper function to convert coordinate systems of input lat/lon
    crs = pyTMD.crs().get(projection)
    x,y = crs.transform(ilon, ilat, direction='FORWARD')
    is_geographic = crs.is_geographic
    # grid step size of tide model
    dx = xi[1] - xi[0]
    dy = yi[1] - yi[0]
    # default bounds if cropping data
    xmin, xmax = np.min(x), np.max(x)
    ymin, ymax = np.min(y), np.max(y)
    bounds = kwargs['bounds'] or [xmin, xmax, ymin, ymax]
    # default buffer if cropping data
    buffer = kwargs['buffer'] or 4*dx

    # crop mask and bathymetry data to (buffered) bounds
    # or adjust longitudinal convention to fit tide model
    if kwargs['crop'] and np.any(bounds):
        mx, my = np.copy(xi), np.copy(yi)
        mz, xi, yi = _crop(mz, mx, my, bounds=bounds,
            buffer=buffer, is_geographic=is_geographic)
        hz, xi, yi = _crop(hz, mx, my, bounds=bounds,
            buffer=buffer, is_geographic=is_geographic)
    elif (np.min(x) < np.min(xi)) & is_geographic:
        # input points convention (-180:180)
        # tide model convention (0:360)
        x[x < 0] += 360.0
    if (np.max(x) > np.max(xi)) & is_geographic:
        # input points convention (0:360)
        # tide model convention (-180:180)
        x[x > 180] -= 360.0

    # if global: extend limits
    is_global = False
    # replace original values with extend arrays/matrices
    if np.isclose(xi[-1] - xi[0], 360.0 - dx) & is_geographic:
        xi = _extend_array(xi, dx)
        # set global grid flag
        is_global = True

    # determine if any input points are outside of the model bounds
    invalid = (x < xi.min()) | (x > xi.max()) | (y < yi.min()) | (y > yi.max())

    # update masks for each type
    if (kwargs['type'] == 'z'):
        # replace original values with extend matrices
        if is_global:
            hz = _extend_matrix(hz)
            mz = _extend_matrix(mz)
        # masks zero values
        mask = (hz == 0) | mz.astype(bool)
        bathymetry = np.ma.array(hz, mask=mask)
    elif kwargs['type'] in ('u','U'):
        # interpolate masks and bathymetry to u, v nodes
        mu,mv = _mask_nodes(hz, is_global=is_global)
        hu,hv = _interpolate_zeta(hz, is_global=is_global)
        # invert current masks to be True for invalid points
        mu = np.logical_not(mu).astype(mu.dtype)
        # replace original values with extend matrices
        if is_global:
            hu = _extend_matrix(hu)
            mu = _extend_matrix(mu)
        # masks zero values
        mask = (hu == 0) | mu.astype(bool)
        bathymetry = np.ma.array(hu, mask=mask)
        # x-coordinates for u transports
        xi -= dx/2.0
    elif kwargs['type'] in ('v','V'):
        # interpolate masks and bathymetry to u, v nodes
        mu,mv = _mask_nodes(hz, is_global=is_global)
        hu,hv = _interpolate_zeta(hz, is_global=is_global)
        # invert current masks to be True for invalid points
        mv = np.logical_not(mv).astype(mv.dtype)
        # replace original values with extend matrices
        if is_global:
            hv = _extend_matrix(hv)
            mv = _extend_matrix(mv)
        # masks zero values
        mask = (hv == 0) | mv.astype(bool)
        bathymetry = np.ma.array(hv, mask=mask)
        # y-coordinates for v transports
        yi -= dy/2.0

    # interpolate bathymetry and mask to output points
    if (kwargs['method'] == 'bilinear'):
        # replace invalid values with nan
        bathymetry.data[bathymetry.mask] = np.nan
        # use quick bilinear to interpolate values
        D = pyTMD.interpolate.bilinear(xi, yi, bathymetry, x, y,
            fill_value=np.ma.default_fill_value(np.dtype(float)))
        # replace nan values with fill_value
        D.mask[:] |= np.isnan(D.data)
        D.data[D.mask] = D.fill_value
    elif (kwargs['method'] == 'spline'):
        # use scipy bivariate splines to interpolate values
        D = pyTMD.interpolate.spline(xi, yi, bathymetry, x, y,
            reducer=np.ceil, kx=1, ky=1)
    else:
        # use scipy regular grid to interpolate values for a given method
        D = pyTMD.interpolate.regulargrid(xi, yi, bathymetry, x, y,
            method=kwargs['method'], reducer=np.ceil, bounds_error=False)

    # u and v: velocities in cm/s
    if kwargs['type'] in ('v','u'):
        unit_conv = (D/100.0)
    # h is elevation values in m
    # U and V are transports in m^2/s
    elif kwargs['type'] in ('z','V','U'):
        unit_conv = 1.0

    # read and interpolate each constituent
    if isinstance(model_file,list):
        constituents = [read_constituents(m)[0].pop() for m in model_file]
        nc = len(constituents)
    else:
        constituents,nc = read_constituents(model_file, grid=kwargs['grid'])

    # number of output data points
    npts = len(D)
    amplitude = np.ma.zeros((npts,nc))
    amplitude.mask = np.zeros((npts,nc), dtype=bool)
    ph = np.ma.zeros((npts,nc))
    ph.mask = np.zeros((npts,nc), dtype=bool)
    # read and interpolate each constituent
    for i,c in enumerate(constituents):
        if (kwargs['type'] == 'z'):
            # read z constituent from elevation file
            if (kwargs['grid'] == 'ATLAS'):
                z0,zlocal = read_atlas_elevation(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, z0, pmask, zlocal,
                    variable='z')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='z')
                # apply flexure scaling
                if kwargs['apply_flexure']:
                    hc *= sf
            elif isinstance(model_file,list):
                hc = read_otis_elevation(model_file[i], 0)
            else:
                hc = read_otis_elevation(model_file, i)
        elif kwargs['type'] in ('U','u'):
            # read u constituent from transport file
            if (kwargs['grid'] == 'ATLAS'):
                u0,v0,uvlocal = read_atlas_transport(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, u0, pmask, uvlocal,
                    variable='u')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='u')
            elif isinstance(model_file,list):
                hc,v = read_otis_transport(model_file[i], 0)
            else:
                hc,v = read_otis_transport(model_file, i)
        elif kwargs['type'] in ('V','v'):
            # read v constituent from transport file
            if (kwargs['grid'] == 'ATLAS'):
                u0,v0,uvlocal = read_atlas_transport(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, v0, pmask, uvlocal,
                    variable='v')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='v')
            elif isinstance(model_file,list):
                u,hc = read_otis_transport(model_file[i], 0)
            else:
                u,hc = read_otis_transport(model_file, i)

        # crop tide model data to (buffered) bounds
        if kwargs['crop'] and np.any(bounds):
            hc, _, _ = _crop(hc, mx, my,
                bounds=bounds, buffer=buffer,
                is_geographic=is_geographic)
        # replace original values with extend matrices
        if is_global:
            hc = _extend_matrix(hc)
        # copy mask to constituent
        hc.mask |= bathymetry.mask

        # interpolate amplitude and phase of the constituent
        if (kwargs['method'] == 'bilinear'):
            # replace zero values with nan
            hc.data[(hc==0) | hc.mask] = np.nan
            # use quick bilinear to interpolate values
            hci = pyTMD.interpolate.bilinear(xi, yi, hc, x, y,
                dtype=hc.dtype)
            # replace nan values with fill_value
            hci.mask = (np.isnan(hci.data) | D.mask)
            hci.data[hci.mask] = hci.fill_value
        elif (kwargs['method'] == 'spline'):
            # use scipy bivariate splines to interpolate values
            hci = pyTMD.interpolate.spline(xi, yi, hc, x, y,
                dtype=hc.dtype,
                reducer=np.ceil,
                kx=1, ky=1)
            # replace zero values with fill_value
            hci.mask |= D.mask
            hci.data[hci.mask] = hci.fill_value
        else:
            # use scipy regular grid to interpolate values
            hci = pyTMD.interpolate.regulargrid(xi, yi, hc, x, y,
                fill_value=hc.fill_value,
                dtype=hc.dtype,
                method=kwargs['method'],
                reducer=np.ceil,
                bounds_error=False)
            # replace invalid values with fill_value
            hci.mask = (hci.data == hci.fill_value) | D.mask
            hci.data[hci.mask] = hci.fill_value
        # extrapolate data using nearest-neighbors
        if kwargs['extrapolate'] and np.any(hci.mask):
            # find invalid data points
            inv, = np.nonzero(hci.mask)
            # replace zero values with nan
            hc.data[(hc==0) | hc.mask] = np.nan
            # extrapolate points within cutoff of valid model points
            hci[inv] = pyTMD.interpolate.extrapolate(xi, yi, hc,
                x[inv], y[inv], dtype=hc.dtype,
                cutoff=kwargs['cutoff'],
                is_geographic=is_geographic)
        # convert units
        # amplitude and phase of the constituent
        amplitude.data[:,i] = np.abs(hci.data)/unit_conv
        amplitude.mask[:,i] = np.copy(hci.mask)
        ph.data[:,i] = np.arctan2(-np.imag(hci), np.real(hci))
        ph.mask[:,i] = np.copy(hci.mask)
        # update mask to invalidate points outside model domain
        ph.mask[:,i] |= invalid
        amplitude.mask[:,i] |= invalid

    # convert phase to degrees
    phase = ph*180.0/np.pi
    phase.data[phase.data < 0] += 360.0
    # replace data for invalid mask values
    amplitude.data[amplitude.mask] = amplitude.fill_value
    phase.data[phase.mask] = phase.fill_value
    # return the interpolated values
    return (amplitude, phase, D, constituents)

# PURPOSE: read harmonic constants from tide models
def read_constants(
        grid_file: str | pathlib.Path | None = None,
        model_file: str | pathlib.Path | list | None = None,
        projection: dict | str | int | None = None,
        **kwargs
    ):
    """
    Reads files from tide models in OTIS and ATLAS-compact formats

    Parameters
    ----------
    grid_file: str, pathlib.Path or NoneType, default None
        grid file for model
    model_file: str, pathlib.Path, list or NoneType, default None
        model file containing each constituent
    projection: str, dict or NoneType, default None,
        projection of tide model data
    type: str, default 'z'
        Tidal variable to read

            - ``'z'``: heights
            - ``'u'``: horizontal transport velocities
            - ``'U'``: horizontal depth-averaged transport
            - ``'v'``: vertical transport velocities
            - ``'V'``: vertical depth-averaged transport
    grid: str, default 'OTIS'
        Tide model file type to read

            - ``'ATLAS'``: reading a global solution with localized solutions
            - ``'OTIS'``: combined global or local solution
            - ``'TMD3'``: combined global or local netCDF4 solution
    crop: bool, default False
        Crop tide model data to (buffered) bounds
    bounds: list or NoneType, default None
        Boundaries for cropping tide model data
    buffer: int or float, default 0
        Buffer angle or distance for cropping tide model data
    apply_flexure: bool, default False
        Apply ice flexure scaling factor to height values

    Returns
    -------
    constituents: obj
        complex form of tide model constituents
    """
    # set default keyword arguments
    kwargs.setdefault('type', 'z')
    kwargs.setdefault('grid', 'OTIS')
    kwargs.setdefault('crop', False)
    kwargs.setdefault('bounds', None)
    kwargs.setdefault('buffer', 0)
    kwargs.setdefault('apply_flexure', False)

    # check that grid file is accessible
    grid_file = pathlib.Path(grid_file).expanduser()
    if not grid_file.exists():
        raise FileNotFoundError(str(grid_file))

    # read the OTIS-format tide grid file
    if (kwargs['grid'] == 'ATLAS'):
        # if reading a global solution with localized solutions
        x0,y0,hz0,mz0,iob,dt,pmask,local = read_atlas_grid(grid_file)
        xi,yi,hz = combine_atlas_model(x0,y0,hz0,pmask,local,variable='depth')
        mz = create_atlas_mask(x0,y0,mz0,local,variable='depth')
    elif (kwargs['grid'] == 'TMD3'):
        # if reading a single TMD3 netCDF4 solution
        xi,yi,hz,mz,sf = read_netcdf_grid(grid_file)
    else:
        # if reading a single OTIS solution
        xi,yi,hz,mz,iob,dt = read_otis_grid(grid_file)
    # invert tide mask to be True for invalid points
    mz = np.logical_not(mz).astype(mz.dtype)
    # grid step size of tide model
    dx = xi[1] - xi[0]
    dy = yi[1] - yi[0]

    # run wrapper function to convert coordinate systems
    # crs = pyTMD.crs().get(projection) # original code with error
    crs = pyTMD.crs.crs().get(projection) # Hung, 28 Feb 25
    # if global: extend limits
    is_geographic = crs.is_geographic

    # crop mask and bathymetry data to (buffered) bounds
    # or adjust longitudinal convention to fit tide model
    if kwargs['crop'] and np.any(kwargs['bounds']):
        # crop tide model data
        mx, my = np.copy(xi), np.copy(yi)
        mz, xi, yi = _crop(mz, mx, my, bounds=kwargs['bounds'],
            buffer=kwargs['buffer'], is_geographic=is_geographic)
        hz, xi, yi = _crop(hz, mx, my, bounds=kwargs['bounds'],
            buffer=kwargs['buffer'], is_geographic=is_geographic)

    # replace original values with extend arrays/matrices
    is_global = False
    if ((xi[-1] - xi[0]) == (360.0 - dx)) & is_geographic:
        xi = _extend_array(xi, dx)
        # set global grid flag
        is_global = True

    # update masks for each type
    # save output constituents
    if (kwargs['type'] == 'z'):
        # replace original values with extend matrices
        if is_global:
            hz = _extend_matrix(hz)
            mz = _extend_matrix(mz)
        # masks zero values
        mask = (hz == 0) | mz.astype(bool)
        bathymetry = np.ma.array(hz, mask=mask)
    elif kwargs['type'] in ('u','U'):
        # interpolate masks and bathymetry to u, v nodes
        mu,mv = _mask_nodes(hz, is_global=is_global)
        hu,hv = _interpolate_zeta(hz, is_global=is_global)
        # invert current masks to be True for invalid points
        mu = np.logical_not(mu).astype(mu.dtype)
        # replace original values with extend matrices
        if is_global:
            hu = _extend_matrix(hu)
            mu = _extend_matrix(mu)
        # masks zero values
        mask = (hu == 0) | mu.astype(bool)
        bathymetry = np.ma.array(hu, mask=mask)
        # x-coordinates for u transports
        xi -= dx/2.0
    elif kwargs['type'] in ('v','V'):
        # interpolate masks and bathymetry to u, v nodes
        mu,mv = _mask_nodes(hz, is_global=is_global)
        hu,hv = _interpolate_zeta(hz, is_global=is_global)
        # invert current masks to be True for invalid points
        mv = np.logical_not(mv).astype(mv.dtype)
        # replace original values with extend matrices
        if is_global:
            hv = _extend_matrix(hv)
            mv = _extend_matrix(mv)
        # masks zero values
        mask = (hv == 0) | mv.astype(bool)
        bathymetry = np.ma.array(hv, mask=mask)
        # y-coordinates for v transports
        yi -= dy/2.0

    # calculate geographic coordinates of model grid
    gridx, gridy = np.meshgrid(xi, yi)
    lon, lat = crs.transform(gridx, gridy, direction='INVERSE')

    # read each constituent
    if isinstance(model_file, list):
        cons = [read_constituents(m)[0].pop() for m in model_file]
    else:
        cons,_ = read_constituents(model_file, grid=kwargs['grid'])
    # save output constituents and coordinate reference system
    constituents = pyTMD.io.constituents(x=xi, y=yi,
        bathymetry=bathymetry.data, mask=mask, crs=crs,
        longitude=lon, latitude=lat)

    # read each model constituent
    for i,c in enumerate(cons):
        if (kwargs['type'] == 'z'):
            # read constituent from elevation file
            if (kwargs['grid'] == 'ATLAS'):
                z0,zlocal = read_atlas_elevation(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, z0, pmask, zlocal,
                    variable='z')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='z')
                # apply flexure scaling
                if kwargs['apply_flexure']:
                    hc *= sf
            elif isinstance(model_file,list):
                hc = read_otis_elevation(model_file[i], 0)
            else:
                hc = read_otis_elevation(model_file, i)
        elif kwargs['type'] in ('U','u'):
            # read constituent from transport file
            if (kwargs['grid'] == 'ATLAS'):
                u0,v0,uvlocal = read_atlas_transport(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, u0, pmask, uvlocal,
                    variable='u')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='u')
            elif isinstance(model_file,list):
                hc,v = read_otis_transport(model_file[i], 0)
            else:
                hc,v = read_otis_transport(model_file, i)
        elif kwargs['type'] in ('V','v'):
            # read constituent from transport file
            if (kwargs['grid'] == 'ATLAS'):
                u0,v0,uvlocal = read_atlas_transport(model_file, i, c)
                _,_,hc = combine_atlas_model(x0, y0, v0, pmask, uvlocal,
                    variable='v')
            elif (kwargs['grid'] == 'TMD3'):
                hc = read_netcdf_file(model_file, i, variable='v')
            elif isinstance(model_file,list):
                u,hc = read_otis_transport(model_file[i], 0)
            else:
                u,hc = read_otis_transport(model_file, i)

        # crop tide model data to (buffered) bounds
        if kwargs['crop'] and np.any(kwargs['bounds']):
            hc, _, _ = _crop(hc, mx, my,
                bounds=kwargs['bounds'],
                buffer=kwargs['buffer'],
                is_geographic=is_geographic)
        # replace original values with extend matrices
        if is_global:
            hc = _extend_matrix(hc)
        # copy mask to constituent
        hc.mask |= bathymetry.mask
        # append extended constituent
        constituents.append(c, hc)

    # return the complex form of the model constituents
    return constituents

# PURPOSE: interpolate constants from tide models to input coordinates
def interpolate_constants(
        ilon: np.ndarray,
        ilat: np.ndarray,
        constituents,
        **kwargs
    ):
    """
    Interpolate constants from OTIS/ATLAS-compact tidal models to input
    coordinates

    Makes initial calculations to run the tide program

    Parameters
    ----------
    ilon: np.ndarray
        longitude to interpolate
    ilat: np.ndarray
        latitude to interpolate
    constituents: obj
        Tide model constituents (complex form)
    type: str, default 'z'
        Tidal variable to read

            - ``'z'``: heights
            - ``'u'``: horizontal transport velocities
            - ``'U'``: horizontal depth-averaged transport
            - ``'v'``: vertical transport velocities
            - ``'V'``: vertical depth-averaged transport
    method: str, default 'spline'
        Interpolation method

            - ``'bilinear'``: quick bilinear interpolation
            - ``'spline'``: scipy bivariate spline interpolation
            - ``'linear'``, ``'nearest'``: scipy regular grid interpolations
    extrapolate: bool, default False
        Extrapolate model using nearest-neighbors
    cutoff: float, default 10.0
        Extrapolation cutoff in kilometers

        Set to ``np.inf`` to extrapolate for all points

    Returns
    -------
    amplitude: np.ndarray
        amplitudes of tidal constituents
    phase: np.ndarray
        phases of tidal constituents
    D: np.ndarray
        bathymetry of tide model
    """
    # set default keyword arguments
    kwargs.setdefault('method', 'spline')
    kwargs.setdefault('extrapolate', False)
    kwargs.setdefault('cutoff', 10.0)
    # verify that constituents are valid class instance
    assert isinstance(constituents, pyTMD.io.constituents)
    # extract model coordinates
    xi = np.copy(constituents.x)
    yi = np.copy(constituents.y)

    # adjust dimensions of input coordinates to be iterable
    ilon = np.atleast_1d(np.copy(ilon))
    ilat = np.atleast_1d(np.copy(ilat))
    # convert coordinate systems of input lat/lon
    x,y = constituents.crs.transform(ilon, ilat)
    is_geographic = constituents.crs.is_geographic
    # adjust longitudinal convention of input latitude and longitude
    # to fit tide model convention
    if (np.min(x) < np.min(xi)) & is_geographic:
        x[x < 0] += 360.0
    if (np.max(x) > np.max(xi)) & is_geographic:
        x[x > 180] -= 360.0
    # determine if any input points are outside of the model bounds
    invalid = (x < xi.min()) | (x > xi.max()) | (y < yi.min()) | (y > yi.max())

    # input model bathymetry
    bathymetry = np.ma.array(constituents.bathymetry)
    bathymetry.mask = np.copy(constituents.mask)
    # interpolate depth and mask to output points
    if (kwargs['method'] == 'bilinear'):
        # use quick bilinear to interpolate values
        D = pyTMD.interpolate.bilinear(xi, yi, bathymetry, x, y)
    elif (kwargs['method'] == 'spline'):
        # use scipy bivariate splines to interpolate values
        D = pyTMD.interpolate.spline(xi, yi, bathymetry, x, y,
            reducer=np.ceil, kx=1, ky=1)
    else:
        # use scipy regular grid to interpolate values for a given method
        D = pyTMD.interpolate.regulargrid(xi, yi, bathymetry, x, y,
            method=kwargs['method'], reducer=np.ceil, bounds_error=False)

    # u and v: velocities in cm/s
    if kwargs['type'] in ('v','u'):
        unit_conv = (D/100.0)
    # h is elevation values in m
    # U and V are transports in m^2/s
    elif kwargs['type'] in ('z','V','U'):
        unit_conv = 1.0

    # number of constituents
    nc = len(constituents)
    # number of output data points
    npts = len(D)
    amplitude = np.ma.zeros((npts,nc))
    amplitude.mask = np.zeros((npts,nc), dtype=bool)
    ph = np.ma.zeros((npts,nc))
    ph.mask = np.zeros((npts,nc), dtype=bool)
    # default complex fill value
    fill_value = np.ma.default_fill_value(np.dtype(complex))
    # interpolate each constituent
    for i, c in enumerate(constituents.fields):
        # get model constituent
        hc = constituents.get(c)
        # interpolate amplitude and phase of the constituent
        if (kwargs['method'] == 'bilinear'):
            # replace zero values with nan
            hc.data[(hc.data == 0) | hc.mask] = np.nan
            # use quick bilinear to interpolate values
            hci = pyTMD.interpolate.bilinear(xi, yi, hc, x, y,
                dtype=hc.dtype)
            # replace nan values with fill_value
            hci.mask = np.isnan(hci.data) | D.mask
            hci.data[hci.mask] = hci.fill_value
        elif (kwargs['method'] == 'spline'):
            # replace zero values with fill value
            hci = pyTMD.interpolate.spline(xi, yi, hc, x, y,
                fill_value=fill_value,
                dtype=hc.dtype,
                reducer=np.ceil,
                kx=1, ky=1)
            # replace zero values with fill_value
            hci.mask = D.mask
            hci.data[hci.mask] = hci.fill_value
        else:
            # replace zero values with fill value
            hc.data[(hc.data == 0) | hc.mask] = fill_value
            # use scipy regular grid to interpolate values
            hci = pyTMD.interpolate.regulargrid(xi, yi, hc, x, y,
                fill_value=fill_value,
                dtype=hc.dtype,
                method=kwargs['method'],
                reducer=np.ceil,
                bounds_error=False)
            # replace invalid values with fill_value
            hci.mask = (hci.data == hci.fill_value) | D.mask
            hci.data[hci.mask] = hci.fill_value
        # extrapolate data using nearest-neighbors
        if kwargs['extrapolate'] and np.any(hci.mask):
            # find invalid data points
            inv, = np.nonzero(hci.mask)
            # replace zero values with nan
            hc.data[(hc==0) | hc.mask] = np.nan
            # extrapolate points within cutoff of valid model points
            hci[inv] = pyTMD.interpolate.extrapolate(xi, yi, hc,
                x[inv], y[inv], dtype=hc.dtype,
                cutoff=kwargs['cutoff'],
                is_geographic=is_geographic)
        # convert units
        # amplitude and phase of the constituent
        amplitude.data[:,i] = np.abs(hci.data)/unit_conv
        amplitude.mask[:,i] = np.copy(hci.mask)
        ph.data[:,i] = np.arctan2(-np.imag(hci), np.real(hci))
        ph.mask[:,i] = np.copy(hci.mask)
        # update mask to invalidate points outside model domain
        ph.mask[:,i] |= invalid
        amplitude.mask[:,i] |= invalid

    # convert phase to degrees
    phase = ph*180.0/np.pi
    phase.data[phase.data < 0] += 360.0
    # replace data for invalid mask values
    amplitude.data[amplitude.mask] = amplitude.fill_value
    phase.data[phase.mask] = phase.fill_value
    # return the interpolated values
    return (amplitude, phase, D)

# PURPOSE: read tide grid file
def read_otis_grid(input_file: str | pathlib.Path):
    """
    Read grid file to extract model coordinates, bathymetry, masks and indices

    Parameters
    ----------
    input_file: str or pathlib.Path
        input grid file

    Returns
    -------
    x: np.ndarray
        x-coordinates of input grid
    y: np.ndarray
        y-coordinates of input grid
    hz: np.ndarray
        model bathymetry
    mz: np.ndarray
        land/water mask
    iob: np.ndarray
        open boundary index
    dt: np.ndarray
        time step
    """
    # open the input file
    input_file = pathlib.Path(input_file).expanduser()
    fid = input_file.open(mode='rb')
    fid.seek(4,0)
    # read data as big endian
    # get model dimensions and limits
    nx, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    ny, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    # extract x and y limits (these could be latitude and longitude)
    ylim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    xlim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    dt, = np.fromfile(fid, dtype=np.dtype('>f4'), count=1)
    # convert longitudinal limits (if x == longitude)
    if (xlim[0] < 0) & (xlim[1] < 0) & (dt > 0):
        xlim += 360.0
    # create x and y arrays arrays (these could be lon and lat values)
    dx = (xlim[1] - xlim[0])/nx
    dy = (ylim[1] - ylim[0])/ny
    x = np.linspace(xlim[0]+dx/2.0, xlim[1]-dx/2.0, nx)
    y = np.linspace(ylim[0]+dy/2.0, ylim[1]-dy/2.0, ny)
    # read nob and iob from file
    nob, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    if (nob == 0):
        fid.seek(20,1)
        iob = []
    else:
        fid.seek(8,1)
        iob=np.fromfile(fid, dtype=np.dtype('>i4'), count=2*nob).reshape(nob, 2)
        fid.seek(8,1)
    # read hz matrix
    hz = np.fromfile(fid, dtype=np.dtype('>f4'), count=nx*ny).reshape(ny, nx)
    fid.seek(8,1)
    # read mz matrix
    mz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nx*ny).reshape(ny, nx)
    # close the file
    fid.close()
    # return values
    return (x, y, hz, mz, iob, dt)

# PURPOSE: read tide grid file with localized solutions
def read_atlas_grid(input_file: str | pathlib.Path):
    """
    Read ATLAS grid file to extract model coordinates, bathymetry, masks and
    indices for both global and local solutions

    Parameters
    ----------
    input_file: str or pathlib.Path
        input ATLAS grid file

    Returns
    -------
    x: np.ndarray
        x-coordinates of input ATLAS grid
    y: np.ndarray
        y-coordinates of input ATLAS grid
    hz: np.ndarray
        model bathymetry
    mz: np.ndarray
        land/water mask
    iob: np.ndarray
        open boundary index
    dt: float
        time step
    pmask: np.ndarray
        global mask
    local: dict
        dictionary of local tidal solutions for grid variables

            - ``'depth'``: model bathymetry
    """
    # open the input file and get file information
    input_file = pathlib.Path(input_file).expanduser()
    file_info = input_file.stat()
    fid = input_file.open(mode='rb')
    fid.seek(4,0)
    # read data as big endian
    # get model dimensions and limits
    nx, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    ny, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    # extract latitude and longitude limits
    lats = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    lons = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    dt, = np.fromfile(fid, dtype=np.dtype('>f4'), count=1)
    # create lon and lat arrays
    dlon = (lons[1] - lons[0])/nx
    dlat = (lats[1] - lats[0])/ny
    x = np.linspace(lons[0]+dlon/2.0,lons[1]-dlon/2.0,nx)
    y = np.linspace(lats[0]+dlat/2.0,lats[1]-dlat/2.0,ny)
    # read nob and iob from file
    nob, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    if (nob == 0):
        fid.seek(20,1)
        iob = []
    else:
        fid.seek(8,1)
        iob=np.fromfile(fid, dtype=np.dtype('>i4'), count=2*nob).reshape(nob, 2)
        fid.seek(8,1)
    # read hz matrix
    hz = np.fromfile(fid, dtype=np.dtype('>f4'), count=nx*ny).reshape(ny, nx)
    fid.seek(8,1)
    # read mz matrix
    mz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nx*ny).reshape(ny, nx)
    fid.seek(8,1)
    # read pmask matrix
    pmask = np.fromfile(fid, dtype=np.dtype('>i4'), count=nx*ny).reshape(ny, nx)
    fid.seek(4,1)
    # read local models
    nmod = 0
    local = {}
    # while the file position is not at the end of file
    while (fid.tell() < file_info.st_size):
        # add 1 to number of models
        fid.seek(4,1)
        nmod += 1
        # get local model dimensions and limits
        nx1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        ny1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nd, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        # extract latitude and longitude limits of local model
        lt1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        ln1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        # extract name
        name = fid.read(20).strip()
        fid.seek(8,1)
        iz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nd)
        jz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nd)
        fid.seek(8,1)
        depth = np.ma.zeros((ny1,nx1))
        depth.mask = np.ones((ny1,nx1), dtype=bool)
        depth.data[jz-1,iz-1] = np.fromfile(fid, dtype=np.dtype('>f4'), count=nd)
        depth.mask[jz-1,iz-1] = False
        fid.seek(4,1)
        # save to dictionary
        local[name] = dict(lon=ln1, lat=lt1, depth=depth)
    # close the file
    fid.close()
    # return values
    return (x, y, hz, mz, iob, dt, pmask, local)

# PURPOSE: read grid file
def read_netcdf_grid(input_file: str | pathlib.Path):
    """
    Read netCDF4 grid file to extract model coordinates, bathymetry,
    masks and flexure scaling factors

    Parameters
    ----------
    input_file: str or pathlib.Path
        input grid file

    Returns
    -------
    x: np.ndarray
        x-coordinates of input grid
    y: np.ndarray
        y-coordinates of input grid
    hz: np.ndarray
        model bathymetry
    mz: np.ndarray
        land/water mask
    sf: np.ndarray
        scaling factor for applying ice flexure
    """
    # tilde-expand input file
    input_file = pathlib.Path(input_file).expanduser()
    # read the netCDF format tide grid file
    fileID = netCDF4.Dataset(input_file, 'r')
    # read coordinates and flip y orientation
    x = fileID.variables['x'][:].copy()
    y = fileID.variables['y'][::-1].copy()
    # read water column thickness and flip y orientation
    hz = fileID.variables['wct'][::-1,:].copy()
    # read mask and flip y orientation
    mz = fileID.variables['mask'][::-1,:].copy()
    # read flexure and convert from percent to scale factor
    sf = fileID.variables['flexure'][::-1,:]/100.0
    # update bathymetry and scale factor masks
    hz.mask = (hz.data == 0.0)
    sf.mask = (sf.data == 0.0)
    # close the grid file
    fileID.close()
    # return values
    return (x, y, hz, mz, sf)

# PURPOSE: read list of constituents from an elevation or transport file
def read_constituents(
        input_file: str | pathlib.Path,
        grid: str = 'OTIS'
    ):
    """
    Read the list of constituents from an elevation or transport file

    Parameters
    ----------
    input_file: str or pathlib.Path
        input tidal file
    grid: str, default 'OTIS'
        Tide model file type to read

            - ``'ATLAS'``: reading a global solution with localized solutions
            - ``'OTIS'``: combined global or local solution
            - ``'TMD3'``: combined global or local netCDF4 solution

    Returns
    -------
    constituents: list
        list of tidal constituent IDs
    nc: int
        number of constituents
    """
    # tilde-expand input file
    input_file = pathlib.Path(input_file).expanduser()
    # check that model file is accessible
    if not input_file.exists():
        raise FileNotFoundError(str(input_file))
    # get the constituents from the input file
    if (grid == 'TMD3'):
        # open the netCDF4 file
        fid = netCDF4.Dataset(input_file, 'r')
        constituents = fid.variables['constituents'].constituent_order.split()
        nc = len(constituents)
        fid.close()
    else:
        # open the file
        fid = input_file.open(mode='rb')
        ll, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nx,ny,nc = np.fromfile(fid, dtype=np.dtype('>i4'), count=3)
        fid.seek(16,1)
        constituents = [c.decode("utf8").rstrip() for c in fid.read(nc*4).split()]
        fid.close()
    return (constituents, nc)

# PURPOSE: read elevation file to extract real and imaginary components for
# constituent
def read_otis_elevation(
        input_file: str | pathlib.Path,
        ic: int
    ):
    """
    Read elevation file to extract real and imaginary components for constituent

    Parameters
    ----------
    input_file: str or pathlib.Path
        input elevation file
    ic: int
        index of constituent

    Returns
    -------
    h: np.ndarray
        tidal elevation
    """
    # open the input file
    input_file = pathlib.Path(input_file).expanduser()
    fid = input_file.open(mode='rb')
    ll, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    nx,ny,nc = np.fromfile(fid, dtype=np.dtype('>i4'), count=3)
    # extract x and y limits
    ylim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    xlim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    # skip records to constituent
    nskip = ic*(int(nx)*int(ny)*8 + 8) + 8 + int(ll) - 28
    fid.seek(nskip,1)
    # real and imaginary components of elevation
    h = np.ma.zeros((ny, nx), dtype=np.complex64)
    h.mask = np.zeros((ny, nx), dtype=bool)
    for i in range(ny):
        temp = np.fromfile(fid, dtype=np.dtype('>f4'), count=2*nx)
        h.data.real[i,:] = temp[0:2*nx-1:2]
        h.data.imag[i,:] = temp[1:2*nx:2]
    # update mask for nan values
    h.mask[np.isnan(h.data)] = True
    # replace masked values with fill value
    h.data[h.mask] = h.fill_value
    # close the file
    fid.close()
    # return the elevation
    return h

# PURPOSE: read elevation file with localized solutions to extract real and
# imaginary components for constituent
def read_atlas_elevation(
        input_file: str | pathlib.Path,
        ic: int,
        constituent: str
    ):
    """
    Read elevation file with localized solutions to extract real and imaginary
    components for constituent

    Parameters
    ----------
    input_file: str or pathlib.Path
        input ATLAS elevation file
    ic: int
        index of constituent
    constituent: str
        tidal constituent ID

    Returns
    -------
    h: float
        global tidal elevation
    local: dict
        dictionary of local tidal solutions for elevation variables

            - ``'z'``: tidal elevation
    """
    # open the input file and get file information
    input_file = pathlib.Path(input_file).expanduser()
    file_info = input_file.stat()
    fid = input_file.open(mode='rb')
    ll, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    nx,ny,nc = np.fromfile(fid, dtype=np.dtype('>i4'), count=3)
    # extract x and y limits
    ylim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    xlim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    # skip records to constituent
    nskip = 8 + int(nc)*4 + int(ic)*(int(nx)*int(ny)*8 + 8)
    fid.seek(nskip,1)
    # real and imaginary components of elevation
    h = np.ma.zeros((ny, nx), dtype=np.complex64)
    h.mask = np.zeros((ny, nx), dtype=bool)
    for i in range(ny):
        temp = np.fromfile(fid, dtype=np.dtype('>f4'), count=2*nx)
        h.data.real[i,:] = temp[0:2*nx-1:2]
        h.data.imag[i,:] = temp[1:2*nx:2]
    # skip records after constituent
    nskip = (int(nc) - int(ic) - 1)*(int(nx)*int(ny)*8 + 8) + 4
    fid.seek(nskip,1)
    # read local models to find constituent
    nmod = 0
    local = {}
    # while the file position is not at the end of file
    while (fid.tell() < file_info.st_size):
        # add 1 to number of models
        fid.seek(4,1)
        nmod += 1
        # get local model dimensions and limits
        nx1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        ny1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nc1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nz, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        # extract latitude and longitude limits of local model
        lt1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        ln1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        # extract constituents for localized solution
        cons = fid.read(nc1*4).strip().decode("utf8").split()
        # check if constituent is in list of localized solutions
        if (constituent in cons):
            ic1, = [i for i,c in enumerate(cons) if (c == constituent)]
            # extract name
            name = fid.read(20).strip()
            fid.seek(8,1)
            iz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nz)
            jz = np.fromfile(fid, dtype=np.dtype('>i4'), count=nz)
            # skip records to constituent
            nskip = 8 + int(ic1)*(8*int(nz) + 8)
            fid.seek(nskip,1)
            # real and imaginary components of elevation
            h1 = np.ma.zeros((ny1,nx1), fill_value=np.nan, dtype=np.complex64)
            h1.mask = np.ones((ny1,nx1), dtype=bool)
            temp = np.fromfile(fid, dtype=np.dtype('>f4'), count=2*nz)
            h1.data.real[jz-1,iz-1] = temp[0:2*nz-1:2]
            h1.data.imag[jz-1,iz-1] = temp[1:2*nz:2]
            h1.mask[jz-1,iz-1] = False
            # save constituent to dictionary
            local[name] = dict(lon=ln1,lat=lt1,z=h1)
            # skip records after constituent
            nskip = (int(nc1) - int(ic1) - 1)*(8*int(nz) + 8) + 4
            fid.seek(nskip,1)
        else:
            # skip records for local model if constituent not in list
            nskip = 40 + 16*int(nz) + (int(nc1) - 1)*(8*int(nz) + 8)
            fid.seek(nskip,1)
    # close the file
    fid.close()
    # return the elevation
    return (h, local)

# PURPOSE: read transport file to extract real and imaginary components for
# constituent
def read_otis_transport(
        input_file: str | pathlib.Path,
        ic: int
    ):
    """
    Read transport file to extract real and imaginary components for constituent

    Parameters
    ----------
    input_file: str or pathlib.Path
        input transport file
    ic: int
        index of constituent

    Returns
    -------
    u: float
        zonal tidal transport
    v: float
        meridional zonal transport
    """
    # open the input file
    input_file = pathlib.Path(input_file).expanduser()
    fid = input_file.open(mode='rb')
    ll, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    nx,ny,nc = np.fromfile(fid, dtype=np.dtype('>i4'), count=3)
    # extract x and y limits
    ylim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    xlim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    # skip records to constituent
    nskip = ic*(int(nx)*int(ny)*16 + 8) + 8 + int(ll) - 28
    fid.seek(nskip,1)
    # real and imaginary components of transport
    u = np.ma.zeros((ny, nx), dtype=np.complex64)
    u.mask = np.zeros((ny, nx), dtype=bool)
    v = np.ma.zeros((ny, nx), dtype=np.complex64)
    v.mask = np.zeros((ny, nx), dtype=bool)
    for i in range(ny):
        temp = np.fromfile(fid, dtype=np.dtype('>f4'), count=4*nx)
        u.data.real[i,:] = temp[0:4*nx-3:4]
        u.data.imag[i,:] = temp[1:4*nx-2:4]
        v.data.real[i,:] = temp[2:4*nx-1:4]
        v.data.imag[i,:] = temp[3:4*nx:4]
    # update mask for nan values
    u.mask[np.isnan(u.data)] = True
    v.mask[np.isnan(v.data)] = True
    # replace masked values with fill value
    u.data[u.mask] = u.fill_value
    v.data[v.mask] = v.fill_value
    # close the file
    fid.close()
    # return the transport components
    return (u, v)

# PURPOSE: read transport file with localized solutions to extract real and
# imaginary components for constituent
def read_atlas_transport(
        input_file: str | pathlib.Path,
        ic: int,
        constituent: str
    ):
    """
    Read transport file with localized solutions to extract real and imaginary
    components for constituent

    Parameters
    ----------
    input_file: str or pathlib.Path
        input ATLAS transport file
    ic: int
        index of constituent
    constituent: str
        tidal constituent ID

    Returns
    -------
    u: np.ndarray
        global zonal tidal transport
    v: np.ndarray
        global meridional zonal transport
    local: dict
        dictionary of local tidal solutions for transport variables

            - ``'u'``: zonal tidal transport
            - ``'v'``: meridional zonal transport
    """
    # open the input file and get file information
    input_file = pathlib.Path(input_file).expanduser()
    file_info = input_file.stat()
    fid = input_file.open(mode='rb')
    ll, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
    nx,ny,nc = np.fromfile(fid, dtype=np.dtype('>i4'), count=3)
    # extract x and y limits
    ylim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    xlim = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
    # skip records to constituent
    nskip = 8 + int(nc)*4 + ic*(int(nx)*int(ny)*16 + 8)
    fid.seek(nskip,1)
    # real and imaginary components of transport
    u = np.ma.zeros((ny, nx), dtype=np.complex64)
    u.mask = np.zeros((ny, nx), dtype=bool)
    v = np.ma.zeros((ny, nx), dtype=np.complex64)
    v.mask = np.zeros((ny, nx), dtype=bool)
    for i in range(ny):
        temp = np.fromfile(fid, dtype=np.dtype('>f4'), count=4*nx)
        u.data.real[i,:] = temp[0:4*nx-3:4]
        u.data.imag[i,:] = temp[1:4*nx-2:4]
        v.data.real[i,:] = temp[2:4*nx-1:4]
        v.data.imag[i,:] = temp[3:4*nx:4]
    # skip records after constituent
    nskip = (int(nc) - int(ic) - 1)*(int(nx)*int(ny)*16 + 8) + 4
    fid.seek(nskip,1)
    # read local models to find constituent
    nmod = 0
    local = {}
    # while the file position is not at the end of file
    while (fid.tell() < file_info.st_size):
        # add 1 to number of models
        fid.seek(4,1)
        nmod += 1
        # get local model dimensions and limits
        nx1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        ny1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nc1, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nu, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        nv, = np.fromfile(fid, dtype=np.dtype('>i4'), count=1)
        # extract latitude and longitude limits of local model
        lt1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        ln1 = np.fromfile(fid, dtype=np.dtype('>f4'), count=2)
        # extract constituents for localized solution
        cons = fid.read(nc1*4).strip().decode("utf8").split()
        # check if constituent is in list of localized solutions
        if (constituent in cons):
            ic1, = [i for i,c in enumerate(cons) if (c == constituent)]
            # extract name
            name = fid.read(20).strip()
            fid.seek(8,1)
            iu = np.fromfile(fid, dtype=np.dtype('>i4'), count=nu)
            ju = np.fromfile(fid, dtype=np.dtype('>i4'), count=nu)
            fid.seek(8,1)
            iv = np.fromfile(fid, dtype=np.dtype('>i4'), count=nv)
            jv = np.fromfile(fid, dtype=np.dtype('>i4'), count=nv)
            # skip records to constituent
            nskip = 8 + int(ic1)*(8*int(nu) + 8*int(nv) + 16)
            fid.seek(nskip,1)
            # real and imaginary components of u transport
            u1 = np.ma.zeros((ny1,nx1), fill_value=np.nan, dtype=np.complex64)
            u1.mask = np.ones((ny1,nx1), dtype=bool)
            tmpu = np.fromfile(fid, dtype=np.dtype('>f4'), count=2*nu)
            u1.data.real[ju-1,iu-1] = tmpu[0:2*nu-1:2]
            u1.data.imag[ju-1,iu-1] = tmpu[1:2*nu:2]
            u1.mask[ju-1,iu-1] = False
            fid.seek(8,1)
            # real and imaginary components of v transport
            v1 = np.ma.zeros((ny1,nx1), fill_value=np.nan, dtype=np.complex64)
            v1.mask = np.ones((ny1,nx1), dtype=bool)
            tmpv = np.fromfile(fid, dtype=np.dtype('>f4'), count=2*nv)
            v1.data.real[jv-1,iv-1] = tmpv[0:2*nv-1:2]
            v1.data.imag[jv-1,iv-1] = tmpv[1:2*nv:2]
            v1.mask[jv-1,iv-1] = False
            # save constituent to dictionary
            local[name] = dict(lon=ln1,lat=lt1,u=u1,v=v1)
            # skip records after constituent
            nskip = (int(nc1) - int(ic1) - 1)*(8*int(nu) + 8*int(nv) + 16) + 4
            fid.seek(nskip,1)
        else:
            # skip records for local model if constituent not in list
            nskip = 56 + 16*int(nu) + 16*int(nv) + \
                (int(nc1) - 1)*(8*int(nu) + 8*int(nv) + 16)
            fid.seek(nskip,1)
    # close the file
    fid.close()
    # return the transport components
    return (u, v, local)

# PURPOSE: create a 2 arc-minute grid mask from mz and depth variables
def create_atlas_mask(
        xi: np.ndarray,
        yi: np.ndarray,
        mz: np.ndarray,
        local: dict,
        variable: str | None = None
    ):
    """
    Creates a high-resolution grid mask from model variables

    Parameters
    ----------
    xi: np.ndarray
        input x-coordinates of global tide model
    yi: np.ndarray
        input y-coordinates of global tide model
    mz: np.ndarray
        global land/water mask
    local: dict
        dictionary of local tidal solutions
    variable: str or NoneType, default None
        key for variable within each local solution

            - ``'depth'``: model bathymetry
            - ``'z'``: tidal elevation
            - ``'u'``: zonal tidal transport
            - ``'v'``: meridional zonal transport

    Returns
    -------
    x30: np.ndarray
        x-coordinates of high-resolution tide model
    y30: np.ndarray
        y-coordinates of high-resolution tide model
    m30: np.ndarray
        high-resolution land/water mask
    """
    # create 2 arc-minute grid dimensions
    d30 = 1.0/30.0
    x30 = np.arange(d30/2.0, 360.0 + d30/2.0, d30)
    y30 = np.arange(-90.0 + d30/2.0, 90.0 + d30/2.0, d30)
    # interpolate global mask to create initial 2 arc-minute mask
    xcoords=np.clip((len(xi)-1)*(x30-xi[0])/(xi[-1]-xi[0]),0,len(xi)-1)
    ycoords=np.clip((len(yi)-1)*(y30-yi[0])/(yi[-1]-yi[0]),0,len(yi)-1)
    IY,IX = np.meshgrid(np.around(ycoords), np.around(xcoords), indexing='ij')
    # interpolate with nearest-neighbors
    m30 = np.ma.zeros((len(y30),len(x30)), dtype=np.int8,fill_value=0)
    m30.data[:,:] = mz[IY.astype(np.int32), IX.astype(np.int32)]
    # iterate over localized solutions to fill in high-resolution coastlines
    for key, val in local.items():
        # shape of local variable
        ny, nx = np.shape(val[variable])
        # correct limits for local grid
        lon0 = np.floor(val['lon'][0]/d30)*d30
        lat0 = np.floor(val['lat'][0]/d30)*d30
        # create latitude and longitude for local model
        xi = lon0 + np.arange(nx)*d30
        yi = lat0 + np.arange(ny)*d30
        IX,IY = np.meshgrid(xi, yi)
        # local model output
        validy,validx = np.nonzero(np.logical_not(val[variable].mask))
        # check if any model longitudes are -180:180
        X = np.where(IX[validy,validx] <= 0.0,
            IX[validy,validx] + 360.0, IX[validy,validx])
        # grid indices of local model
        ii = ((X - x30[0])//d30).astype('i')
        jj = ((IY[validy,validx] - y30[0])//d30).astype('i')
        # fill global mask with regional solution
        m30[jj,ii] = 1
    # return the 2 arc-minute mask
    m30.mask = (m30.data == m30.fill_value)
    return m30

# PURPOSE: resample global solution to higher-resolution
def interpolate_atlas_model(
        xi: np.ndarray,
        yi: np.ndarray,
        zi: np.ndarray,
        spacing: float = 1.0/30.0
    ):
    """
    Interpolates global ATLAS tidal solutions into a
    higher-resolution sampling

    Parameters
    ----------
    xi: np.ndarray
        input x-coordinates of global tide model
    yi: np.ndarray
        input y-coordinates of global tide model
    zi: np.ndarray
        global tide model data
    spacing: float
        output grid spacing

    Returns
    -------
    xs: np.ndarray
        x-coordinates of high-resolution tide model
    ys: np.ndarray
        y-coordinates of high-resolution tide model
    zs: np.ndarray
        high-resolution tidal solution for variable
    """
    # create resampled grid dimensions
    xs = np.arange(spacing/2.0, 360.0 + spacing/2.0, spacing)
    ys = np.arange(-90.0 + spacing/2.0, 90.0 + spacing/2.0, spacing)
    # interpolate global solution
    zs = np.ma.zeros((len(ys),len(xs)), dtype=zi.dtype)
    zs.mask = np.zeros((len(ys),len(xs)), dtype=bool)
    # test if combining elevation/transport variables with complex components
    if np.iscomplexobj(zs):
        f1 = scipy.interpolate.RectBivariateSpline(xi, yi, zi.real.T, kx=1,ky=1)
        f2 = scipy.interpolate.RectBivariateSpline(xi, yi, zi.imag.T, kx=1,ky=1)
        zs.data.real[:,:] = f1(xs,ys).T
        zs.data.imag[:,:] = f2(xs,ys).T
    else:
        f = scipy.interpolate.RectBivariateSpline(xi, yi, zi.T, kx=1,ky=1)
        zs.data[:,:] = f(xs,ys).T
    # return resampled solution and coordinates
    return (xs, ys, zs)

# PURPOSE: combines global and local atlas solutions
def combine_atlas_model(
        xi: np.ndarray,
        yi: np.ndarray,
        zi: np.ndarray,
        pmask: np.ndarray,
        local: dict,
        variable: str | None = None
    ):
    """
    Combines global and local ATLAS tidal solutions into a single
    high-resolution solution

    Parameters
    ----------
    xi: np.ndarray
        input x-coordinates of global tide model
    yi: np.ndarray
        input y-coordinates of global tide model
    zi: np.ndarray
        global tide model data
    pmask: np.ndarray
        global mask
    local: dict
        dictionary of local tidal solutions
    variable: str or NoneType, default None
        key for variable within each local solution

            - ``'depth'``: model bathymetry
            - ``'z'``: tidal elevation
            - ``'u'``: zonal tidal transport
            - ``'v'``: meridional zonal transport

    Returns
    -------
    x30: np.ndarray
        x-coordinates of high-resolution tide model
    y30: np.ndarray
        y-coordinates of high-resolution tide model
    z30: np.ndarray
        combined high-resolution tidal solution for variable
    """
    # create 2 arc-minute grid dimensions
    d30 = 1.0/30.0
    # interpolate global solution to 2 arc-minute solution
    x30, y30, z30 = interpolate_atlas_model(xi, yi, zi, spacing=d30)
    # iterate over localized solutions
    for key,val in local.items():
        # shape of local variable
        ny, nx = np.shape(val[variable])
        # correct limits for local grid
        lon0 = np.floor(val['lon'][0]/d30)*d30
        lat0 = np.floor(val['lat'][0]/d30)*d30
        # create latitude and longitude for local model
        xi = lon0 + np.arange(nx)*d30
        yi = lat0 + np.arange(ny)*d30
        IX,IY = np.meshgrid(xi,yi)
        # local model output
        validy,validx = np.nonzero(np.logical_not(val[variable].mask))
        # check if any model longitudes are -180:180
        X = np.where(IX[validy,validx] <= 0.0,
            IX[validy,validx] + 360.0, IX[validy,validx])
        # grid indices of local model
        ii = ((X - x30[0])//d30).astype('i')
        jj = ((IY[validy,validx] - y30[0])//d30).astype('i')
        # fill global mask with regional solution
        z30.data[jj,ii] = val[variable][validy,validx]
    # return 2 arc-minute solution and coordinates
    return (x30, y30, z30)

# PURPOSE: read netCDF4 file to extract real and imaginary components for
# constituent
def read_netcdf_file(
        input_file: str | pathlib.Path,
        ic: int,
        variable: str | None = None
    ):
    """
    Read netCDF4 file to extract real and imaginary components for constituent

    Parameters
    ----------
    input_file: str or pathlib.Path
        input transport file
    ic: int
        index of constituent
    variable: str or NoneType, default None
        Tidal variable to read

            - ``'z'``: heights
            - ``'u'``: horizontal transport velocities
            - ``'U'``: horizontal depth-averaged transport
            - ``'v'``: vertical transport velocities
            - ``'V'``: vertical depth-averaged transport

    Returns
    -------
    hc: complex
        complex form of tidal constituent oscillation
    """
    # tilde-expand input file
    input_file = pathlib.Path(input_file).expanduser()
    # read the netcdf format tide grid file
    fileID = netCDF4.Dataset(input_file, 'r')
    # variable dimensions
    nx = fileID.dimensions['x'].size
    ny = fileID.dimensions['y'].size
    # real and imaginary components of tidal constituent
    hc = np.ma.zeros((ny, nx), dtype=np.complex64)
    hc.mask = np.zeros((ny, nx), dtype=bool)
    # extract constituent and flip y orientation
    if (variable == 'z'):
        hc.data.real[:,:] = fileID.variables['hRe'][ic,::-1,:]
        hc.data.imag[:,:] = -fileID.variables['hIm'][ic,::-1,:]
    elif variable in ('U','u'):
        hc.data.real[:,:] = fileID.variables['URe'][ic,::-1,:]
        hc.data.imag[:,:] = -fileID.variables['UIm'][ic,::-1,:]
    elif variable in ('V','v'):
        hc.data.real[:,:] = fileID.variables['VRe'][ic,::-1,:]
        hc.data.imag[:,:] = -fileID.variables['VIm'][ic,::-1,:]
    # close the file
    fileID.close()
    # return output variables
    return hc

# PURPOSE: output grid file in OTIS format
def output_otis_grid(
        FILE: str | pathlib.Path,
        xlim: np.ndarray | list,
        ylim: np.ndarray | list,
        hz: np.ndarray,
        mz: np.ndarray,
        iob: np.ndarray,
        dt: float
    ):
    """
    Writes OTIS-format grid files

    Parameters
    ----------
    FILE: str or pathlib.Path
        output OTIS grid file name
    xlim: np.ndarray
        x-coordinate grid-cell edges of output grid
    ylim: np.ndarray
        y-coordinate grid-cell edges of output grid
    hz: np.ndarray
        bathymetry
    mz: np.ndarray
        land/water mask
    iob: np.ndarray
        open boundary index
    dt: float
        time step
    """
    # tilde-expand output file
    FILE = pathlib.Path(FILE).expanduser()
    # open output file
    fid = FILE.open(mode='wb')
    nob = len(iob)
    ny, nx = np.shape(hz)
    reclen = 32
    fid.write(struct.pack('>i',reclen))
    fid.write(struct.pack('>i',nx))
    fid.write(struct.pack('>i',ny))
    ylim.tofile(fid,format='>f4')
    xlim.tofile(fid,format='>f4')
    fid.write(struct.pack('>f',dt))
    fid.write(struct.pack('>i',nob))
    fid.write(struct.pack('>i',reclen))
    if (nob == 0):
        fid.write(struct.pack('>i',4))
        fid.write(struct.pack('>i',0))
        fid.write(struct.pack('>i',4))
    else:
        reclen = 8*nob
        fid.write(struct.pack('>i',reclen))
        iob.tofile(fid,format='>i4')
        fid.write(struct.pack('>i',reclen))
    reclen = 4*nx*ny
    # write depth and mask data to file
    fid.write(struct.pack('>i',reclen))
    hz.tofile(fid,format='>f4')
    for m in range(ny):
        hz[m,:].tofile(fid,format='>f4')
    fid.write(struct.pack('>i',reclen))
    fid.write(struct.pack('>i',reclen))
    for m in range(ny):
        mz[m,:].tofile(fid,format='>i4')
    fid.write(struct.pack('>i',reclen))
    # close the output OTIS file
    fid.close()

# PURPOSE: output elevation file in OTIS format
def output_otis_elevation(
        FILE: str | pathlib.Path,
        h: np.ndarray,
        xlim: np.ndarray | list,
        ylim: np.ndarray | list,
        constituents: list
    ):
    """
    Writes OTIS-format elevation files

    Parameters
    ----------
    FILE: str or pathlib.Path
        output OTIS elevation file name
    h: np.ndarray
        Eulerian form of tidal height oscillation
    xlim: np.ndarray
        x-coordinate grid-cell edges of output grid
    ylim: np.ndarray
        y-coordinate grid-cell edges of output grid
    constituents: list
        tidal constituent IDs
    """
    # tilde-expand output file
    FILE = pathlib.Path(FILE).expanduser()
    # open output file
    fid = FILE.open(mode='wb')
    ny, nx, nc = np.shape(h)
    # length of header: allow for 4 character >i c_id strings
    header_length = 4*(7 + nc)
    fid.write(struct.pack('>i',header_length))
    fid.write(struct.pack('>i',nx))
    fid.write(struct.pack('>i',ny))
    fid.write(struct.pack('>i',nc))
    ylim.tofile(fid,format='>f4')
    xlim.tofile(fid,format='>f4')
    for c in constituents:
        fid.write(c.ljust(4).encode('utf8'))
    fid.write(struct.pack('>i',header_length))
    # write each constituent to file
    constituent_header = 8*nx*ny
    for ic in range(nc):
        fid.write(struct.pack('>i',constituent_header))
        for m in range(ny):
            temp = np.zeros((2*nx),dtype='>f')
            temp[0:2*nx-1:2] = h.real[m,:,ic]
            temp[1:2*nx:2] = h.imag[m,:,ic]
            temp.tofile(fid,format='>f4')
        fid.write(struct.pack('>i',constituent_header))
    # close the output OTIS file
    fid.close()

# PURPOSE: output transport file in OTIS format
def output_otis_transport(
        FILE: str | pathlib.Path,
        u: np.ndarray,
        v: np.ndarray,
        xlim: np.ndarray | list,
        ylim: np.ndarray | list,
        constituents: list
    ):
    """
    Writes OTIS-format transport files

    Parameters
    ----------
    FILE: str or pathlib.Path
        output OTIS transport file name
    u: complex
        Eulerian form of tidal zonal transport oscillation
    v: complex
        Eulerian form of tidal meridional transport oscillation
    xlim: float
        x-coordinate grid-cell edges of output grid
    ylim: float
        y-coordinate grid-cell edges of output grid
    constituents: list
        tidal constituent IDs
    """
    # tilde-expand output file
    FILE = pathlib.Path(FILE).expanduser()
    # open output file
    fid = FILE.open(mode='wb')
    ny, nx, nc = np.shape(u)
    # length of header: allow for 4 character >i c_id strings
    header_length = 4*(7 + nc)
    fid.write(struct.pack('>i',header_length))
    fid.write(struct.pack('>i',nx))
    fid.write(struct.pack('>i',ny))
    fid.write(struct.pack('>i',nc))
    ylim.tofile(fid,format='>f4')
    xlim.tofile(fid,format='>f4')
    for c in constituents:
        fid.write(c.ljust(4).encode('utf8'))
    fid.write(struct.pack('>i',header_length))
    # write each constituent to file
    constituent_header = 2*8*nx*ny
    for ic in range(nc):
        fid.write(struct.pack('>i',constituent_header))
        for m in range(ny):
            temp = np.zeros((4*nx),dtype='>f')
            temp[0:4*nx-3:4] = u.real[m,:,ic]
            temp[1:4*nx-2:4] = u.imag[m,:,ic]
            temp[2:4*nx-1:4] = v.real[m,:,ic]
            temp[3:4*nx:4] = v.imag[m,:,ic]
            temp.tofile(fid,format='>f4')
        fid.write(struct.pack('>i',constituent_header))
    # close the output OTIS file
    fid.close()

# PURPOSE: Extend a longitude array
def _extend_array(input_array: np.ndarray, step_size: float):
    """
    Extends a longitude array

    Parameters
    ----------
    input_array: np.ndarray
        array to extend
    step_size: float
        step size between elements of array

    Returns
    -------
    temp: np.ndarray
        extended array
    """
    n = len(input_array)
    temp = np.zeros((n+2), dtype=input_array.dtype)
    # extended array [x-1,x0,...,xN,xN+1]
    temp[0] = input_array[0] - step_size
    temp[1:-1] = input_array[:]
    temp[-1] = input_array[-1] + step_size
    return temp

# PURPOSE: Extend a global matrix
def _extend_matrix(input_matrix: np.ndarray):
    """
    Extends a global matrix

    Parameters
    ----------
    input_matrix: np.ndarray
        matrix to extend

    Returns
    -------
    temp: np.ndarray
        extended matrix
    """
    ny, nx = np.shape(input_matrix)
    # allocate for extended matrix
    if np.ma.isMA(input_matrix):
        temp = np.ma.zeros((ny, nx+2), dtype=input_matrix.dtype)
    else:
        temp = np.zeros((ny, nx+2), dtype=input_matrix.dtype)
    # extend matrix
    temp[:,0] = input_matrix[:,-1]
    temp[:,1:-1] = input_matrix[:,:]
    temp[:,-1] = input_matrix[:,0]
    return temp

# PURPOSE: crop data to bounds
def _crop(
        input_matrix: np.ndarray,
        ix: np.ndarray,
        iy: np.ndarray,
        bounds: list | tuple,
        buffer: int | float = 0,
        is_geographic: bool = True,
    ):
    """
    Crop tide model data to bounds

    Parameters
    ----------
    input_matrix: np.ndarray
        matrix to crop
    ix: np.ndarray
        x-coordinates of input grid
    iy: np.ndarray
        y-coordinates of input grid
    bounds: list, tuple
        bounding box: ``[xmin, xmax, ymin, ymax]``
    buffer: int or float, default 0
        buffer to add to bounds for cropping
    is_geographic: bool, default True
        input grid is in geographic coordinates

    Returns
    -------
    temp: np.ndarray
        cropped matrix
    x: np.ndarray
        cropped x-coordinates
    y: np.ndarray
        cropped y-coordinates
    """
    # adjust longitudinal convention of tide model
    if is_geographic & (np.min(bounds[:2]) < 0.0) & (np.max(ix) > 180.0):
        input_matrix, ix, = _shift(input_matrix, ix,
            x0=180.0, cyclic=360.0, direction='west')
    elif is_geographic & (np.max(bounds[:2]) > 180.0) & (np.min(ix) < 0.0):
        input_matrix, ix, = _shift(input_matrix, ix,
            x0=0.0, cyclic=360.0, direction='east')
    # unpack bounds and buffer
    xmin = bounds[0] - buffer
    xmax = bounds[1] + buffer
    ymin = bounds[2] - buffer
    ymax = bounds[3] + buffer
    # find indices for cropping
    yind = np.flatnonzero((iy >= ymin) & (iy <= ymax))
    xind = np.flatnonzero((ix >= xmin) & (ix <= xmax))
    # slices for cropping axes
    rows = slice(yind[0], yind[-1]+1)
    cols = slice(xind[0], xind[-1]+1)
    # crop matrix
    temp = input_matrix[rows, cols]
    x = ix[cols]
    y = iy[rows]
    # return cropped data
    return (temp, x, y)

# PURPOSE: shift a grid east or west
def _shift(
        input_matrix: np.ndarray,
        ix: np.ndarray,
        x0: int | float = 180,
        cyclic: int | float = 360,
        direction: str = 'west'
    ):
    """
    Shift global grid east or west to a new base longitude

    Parameters
    ----------
    input_matrix: np.ndarray
        matrix to crop
    ix: np.ndarray
        x-coordinates of input grid
    lon0: int or float, default 180
        Starting longitude for shifted grid
    cyclic: int or float, default 360
        width of periodic domain
    direction: str, default 'west'
        Direction to shift grid

            - ``'west'``
            - ``'east'``

    Returns
    -------
    temp: np.ndarray
        shifted matrix
    x: np.ndarray
        shifted x-coordinates
    """
    # find the starting index if cyclic
    offset = 0 if (np.fabs(ix[-1]-ix[0]-cyclic) > 1e-4) else 1
    i0 = np.argmin(np.fabs(ix - x0))
    # shift longitudinal values
    x = np.zeros(ix.shape, ix.dtype)
    x[0:-i0] = ix[i0:]
    x[-i0:] = ix[offset: i0+offset]
    # add or remove the cyclic
    if (direction == 'east'):
        x[-i0:] += cyclic
    elif (direction == 'west'):
        x[0:-i0] -= cyclic
    # allocate for shifted data
    if np.ma.isMA(input_matrix):
        temp = np.ma.zeros(input_matrix.shape,input_matrix.dtype)
    else:
        temp = np.zeros(input_matrix.shape, input_matrix.dtype)
    # shift data values
    temp[:,:-i0] = input_matrix[:,i0:]
    temp[:,-i0:] = input_matrix[:,offset: i0+offset]
    # return the shifted values
    return (temp, x)

# PURPOSE: construct masks for u and v nodes
def _mask_nodes(hz: np.ndarray, is_global: bool = True):
    """
    Construct masks for u and v nodes on a C-grid

    Parameters
    ----------
    hz: np.ndarray
        bathymetry of grid centers
    is_global: bool, default True
        input grid is global in terms of longitude
    """
    # for grid center mask: find where bathymetry is greater than 0
    mz = (hz > 0).astype(int)
    mu, mv = _interpolate_mask(mz, is_global=is_global)
    # return the masks
    return (mu, mv)

# PURPOSE: interpolate mask to u and v nodes
def _interpolate_mask(mz: np.ndarray, is_global: bool = True):
    """
    Interpolate mask from zeta nodes to u and v nodes on a C-grid

    Parameters
    ----------
    mz: np.ndarray
        mask at grid centers
    is_global: bool, default True
        input grid is global in terms of longitude
    """
    # shape of input mask
    ny, nx = np.shape(mz)
    # initialize integer masks for u and v grids
    mu = np.zeros((ny, nx), dtype=int)
    mv = np.zeros((ny, nx), dtype=int)
    # wrap mask if global
    mode = 'wrap' if is_global else 'edge'
    # calculate masks on u and v grids
    tmp = np.pad(mz, ((0, 0), (1, 0)), mode=mode)
    mu[:,:] = (tmp[:,:-1]*tmp[:,1:])
    tmp = np.pad(mz, ((1, 0), (0, 0)), mode='edge')
    mv[:,:] = (tmp[:-1,:]*tmp[1:,:])
    # return the masks
    return (mu, mv)

# PURPOSE: interpolate data to u and v nodes
def _interpolate_zeta(hz: np.ndarray, is_global: bool = True):
    """
    Interpolate data from zeta nodes to u and v nodes on a C-grid

    Parameters
    ----------
    hz: np.ndarray
        data at grid centers
    is_global: bool, default True
        input grid is global in terms of longitude
    """
    # shape of input data
    ny, nx = np.shape(hz)
    # get masks for u and v nodes
    mu, mv = _mask_nodes(hz)
    # initialize data for u and v grids
    hu = np.zeros((ny, nx), dtype=hz.dtype)
    hv = np.zeros((ny, nx), dtype=hz.dtype)
    # wrap data if global
    mode = 'wrap' if is_global else 'edge'
    # calculate data at u and v nodes
    tmp = np.pad(hz, ((0, 0), (1, 0)), mode=mode)
    hu[:,:] = 0.5*mu*(tmp[:,:-1] + tmp[:,1:])
    tmp = np.pad(hz, ((1, 0), (0, 0)), mode='edge')
    hv[:,:] = 0.5*mv*(tmp[:-1,:] + tmp[1:,:])
    # return the interpolated data values
    return (hu, hv)
