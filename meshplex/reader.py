# -*- coding: utf-8 -*-
#
"""
Module for reading unstructured grids (and related data) from various file
formats.

.. moduleauthor:: Nico Schlömer <nico.schloemer@gmail.com>
"""
import numpy

import meshio
from .mesh_tri import MeshTri
from .mesh_tetra import MeshTetra


__all__ = ["read"]


def _sanitize(points, cells):
    uvertices, uidx = numpy.unique(cells, return_inverse=True)
    cells = uidx.reshape(cells.shape)
    points = points[uvertices]
    return points, cells


def read(filename):
    """Reads an unstructured mesh with added data.

    :param filenames: The files to read from.
    :type filenames: str
    :returns mesh{2,3}d: The mesh data.
    :returns point_data: Point data read from file.
    :type point_data: dict
    :returns field_data: Field data read from file.
    :type field_data: dict
    """
    mesh = meshio.read(filename)

    # make sure to include the used nodes only
    if "tetra" in mesh.cells:
        points, cells = _sanitize(mesh.points, mesh.cells["tetra"])
        return (
            MeshTetra(points, cells),
            mesh.point_data,
            mesh.cell_data,
            mesh.field_data,
        )
    elif "triangle" in mesh.cells:
        points, cells = _sanitize(mesh.points, mesh.cells["triangle"])
        return (
            MeshTri(points, cells),
            mesh.point_data,
            mesh.cell_data,
            mesh.field_data,
        )
    else:
        raise RuntimeError("Unknown mesh type.")
