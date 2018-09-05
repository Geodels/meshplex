# -*- coding: utf-8 -*-
#
import os

import numpy
# import fastfunc

from .base import (
    _base_mesh,
    compute_tri_areas_and_ce_ratios,
    compute_triangle_circumcenters,
)
from .helpers import grp_start_len, unique_rows


__all__ = ["MeshTri"]


def _column_stack(a, b):
    # https://stackoverflow.com/a/39638773/353337
    return numpy.stack([a, b], axis=1)


class MeshTri(_base_mesh):
    """Class for handling triangular meshes.

    .. inheritance-diagram:: MeshTri
    """

    def __init__(self, nodes, cells, sort_cells=False):
        """Initialization.
        """
        if sort_cells:
            # Sort cells and nodes, first every row, then the rows themselves.  This
            # helps in many downstream applications, e.g., when constructing linear
            # systems with the cells/edges. (When converting to CSR format, the I/J
            # entries must be sorted.) Don't use cells.sort(axis=1) to avoid
            # ```
            # ValueError: sort array is read-only
            # ```
            cells = numpy.sort(cells, axis=1)
            cells = cells[cells[:, 0].argsort()]

        assert len(nodes.shape) == 2, "Illegal node coordinates shape {}".format(
            nodes.shape
        )
        assert (
            len(cells.shape) == 2 and cells.shape[1] == 3
        ), "Illegal cells shape {}".format(cells.shape)

        super(MeshTri, self).__init__(nodes, cells)

        # Assert that all vertices are used.
        # If there are vertices which do not appear in the cells list, this
        # ```
        # uvertices, uidx = numpy.unique(cells, return_inverse=True)
        # cells = uidx.reshape(cells.shape)
        # nodes = nodes[uvertices]
        # ```
        # helps.
        self.node_is_used = numpy.zeros(len(nodes), dtype=bool)
        self.node_is_used[cells] = True

        self.cells = {"nodes": cells}

        self._interior_ce_ratios = None
        self._control_volumes = None
        self._cell_partitions = None
        self._cv_centroids = None
        self._surface_areas = None
        self.edges = None
        self._cell_circumcenters = None
        self._signed_cell_areas = None
        self.subdomains = {}
        self._is_interior_node = None
        self._is_boundary_node = None
        self.is_boundary_edge = None
        self._is_boundary_facet = None
        self._interior_edge_lengths = None
        self._ce_ratios = None
        self._edges_cells = None
        self._edge_gid_to_edge_list = None
        self._edge_to_edge_gid = None
        self._cell_centroids = None

        # compute data
        # Create the idx_hierarchy (nodes->edges->cells), i.e., the value of
        # `self.idx_hierarchy[0, 2, 27]` is the index of the node of cell 27,
        # edge 2, node 0. The shape of `self.idx_hierarchy` is `(2, 3, n)`,
        # where `n` is the number of cells. Make sure that the k-th edge is
        # opposite of the k-th point in the triangle.
        self.local_idx = numpy.array([[1, 2], [2, 0], [0, 1]]).T
        # Map idx back to the nodes. This is useful if quantities which are in
        # idx shape need to be added up into nodes (e.g., equation system rhs).
        nds = self.cells["nodes"].T
        self.idx_hierarchy = nds[self.local_idx]

        # The inverted local index.
        # This array specifies for each of the three nodes which edge endpoints
        # correspond to it. For the above local_idx, this should give
        #
        #    [[(1, 1), (0, 2)], [(0, 0), (1, 2)], [(1, 0), (0, 1)]]
        #
        self.local_idx_inv = [
            [tuple(i) for i in zip(*numpy.where(self.local_idx == k))] for k in range(3)
        ]

        # Create the corresponding edge coordinates.
        self.half_edge_coords = (
            self.node_coords[self.idx_hierarchy[1]]
            - self.node_coords[self.idx_hierarchy[0]]
        )

        self.ei_dot_ej = numpy.einsum(
            "ijk, ijk->ij",
            self.half_edge_coords[[1, 2, 0]],
            self.half_edge_coords[[2, 0, 1]],
        )

        e = self.half_edge_coords
        self.ei_dot_ei = numpy.einsum("ijk, ijk->ij", e, e)

        self.cell_volumes, self.ce_ratios = compute_tri_areas_and_ce_ratios(
            self.ei_dot_ej
        )

        # self.fcc_type = "full"
        # is_flat_halfedge = self.ce_ratios < 0.0
        # flat_local_edge, self.flat_cells = numpy.where(is_flat_halfedge)
        # self.is_flat_cell = numpy.any(is_flat_halfedge, axis=0)
        # self.fcc = FlatCellCorrector(
        #     self.cells["nodes"][self.fcc_cells], flat_local_edge, self.node_coords
        # )
        # self.ce_ratios[:, self.fcc_cells] = self.fcc.ce_ratios.T

        return

    # def update_node_coordinates(self, X):
    #     assert X.shape == self.node_coords.shape
    #     self.node_coords = X
    #     self._update_values()
    #     return

    # def update_interior_node_coordinates(self, X):
    #     assert X.shape == self.node_coords[self.is_interior_node].shape
    #     self.node_coords[self.is_interior_node] = X
    #     self.update_values()
    #     return

    def update_values(self):
        if self.half_edge_coords is not None:
            # Constructing the temporary arrays
            # self.node_coords[self.idx_hierarchy] can take quite a while here.
            self.half_edge_coords = (
                self.node_coords[self.idx_hierarchy[1]]
                - self.node_coords[self.idx_hierarchy[0]]
            )

        if self.ei_dot_ej is not None:
            self.ei_dot_ej = numpy.einsum(
                "ijk, ijk->ij",
                self.half_edge_coords[[1, 2, 0]],
                self.half_edge_coords[[2, 0, 1]],
            )

        if self.ei_dot_ei is not None:
            e = self.half_edge_coords
            self.ei_dot_ei = numpy.einsum("ijk, ijk->ij", e, e)

        if self.cell_volumes is not None or self.ce_ratios is not None:
            self.cell_volumes, self.ce_ratios = compute_tri_areas_and_ce_ratios(
                self.ei_dot_ej
            )

        self._interior_edge_lengths = None
        self._cell_circumcenters = None
        self._interior_ce_ratios = None
        self._control_volumes = None
        self._cell_partitions = None
        self._cv_centroids = None
        self._surface_areas = None
        self._signed_cell_areas = None
        self._cell_centroids = None
        return

    @property
    def ce_ratios_per_interior_edge(self):
        if self._interior_ce_ratios is None:
            if "edges" not in self.cells:
                self.create_edges()

            self._ce_ratios = numpy.zeros(len(self.edges["nodes"]))
            numpy.add.at(self._ce_ratios, self.cells["edges"].T, self.ce_ratios)
            # fastfunc.add.at(self._ce_ratios, self.cells["edges"].T, self.ce_ratios)
            self._interior_ce_ratios = self._ce_ratios[
                ~self.is_boundary_edge_individual
            ]

            # # sum up from self.ce_ratios
            # if self._edges_cells is None:
            #     self._compute_edges_cells()

            # self._interior_ce_ratios = \
            #     numpy.zeros(self._edges_local[2].shape[0])
            # for i in [0, 1]:
            #     # Interior edges = edges with _2_ adjacent cells
            #     idx = [
            #         self._edges_local[2][:, i],
            #         self._edges_cells[2][:, i],
            #         ]
            #     self._interior_ce_ratios += self.ce_ratios[idx]

        return self._interior_ce_ratios

    @property
    def control_volumes(self):
        if self._control_volumes is None:
            v = self.cell_partitions

            # Summing up the arrays first makes the work for numpy.add.at
            # lighter.
            ids = self.cells["nodes"].T
            vals = numpy.array(
                [
                    sum([v[i] for i in numpy.where(self.local_idx.T == k)[0]])
                    for k in range(3)
                ]
            )
            control_volume_data = [(ids, vals)]

            # sum up from self.control_volume_data
            self._control_volumes = numpy.zeros(len(self.node_coords))
            for d in control_volume_data:
                # TODO fastfunc
                numpy.add.at(self._control_volumes, d[0], d[1])

        return self._control_volumes

    @property
    def surface_areas(self):
        if self._surface_areas is None:
            self._surface_areas = self._compute_surface_areas()
        return self._surface_areas

    @property
    def control_volume_centroids(self):
        # This function is necessary, e.g., for Lloyd's
        # smoothing <https://en.wikipedia.org/wiki/Lloyd%27s_algorithm>.
        #
        # The centroid of any volume V is given by
        #
        #   c = \int_V x / \int_V 1.
        #
        # The denominator is the control volume. The numerator can be computed
        # by making use of the fact that the control volume around any vertex
        # v_0 is composed of right triangles, two for each adjacent cell.
        if self._cv_centroids is None:
            _, v = self._compute_integral_x()
            # Again, make use of the fact that edge k is opposite of node k in
            # every cell. Adding the arrays first makes the work for
            # numpy.add.at lighter.
            ids = self.cells["nodes"].T
            vals = numpy.array(
                [v[1, 1] + v[0, 2], v[1, 2] + v[0, 0], v[1, 0] + v[0, 1]]
            )
            centroid_data = [(ids, vals)]
            # add it all up
            num_components = centroid_data[0][1].shape[-1]
            self._cv_centroids = numpy.zeros((len(self.node_coords), num_components))
            for d in centroid_data:
                # TODO fastfunc
                numpy.add.at(self._cv_centroids, d[0], d[1])
            # Divide by the control volume
            self._cv_centroids /= self.control_volumes[:, None]

        return self._cv_centroids

    @property
    def signed_cell_areas(self):
        """Signed area of a triangle in 2D.
        """
        # http://mathworld.wolfram.com/TriangleArea.html
        assert (
            self.node_coords.shape[1] == 2
        ), "Signed areas only make sense for triangles in 2D."

        if self._signed_cell_areas is None:
            # One could make p contiguous by adding a copy(), but that's not
            # really worth it here.
            p = self.node_coords[self.cells["nodes"]].T
            # <https://stackoverflow.com/q/50411583/353337>
            self._signed_cell_areas = (
                +p[0][2] * (p[1][0] - p[1][1])
                + p[0][0] * (p[1][1] - p[1][2])
                + p[0][1] * (p[1][2] - p[1][0])
            ) / 2
        return self._signed_cell_areas

    def mark_boundary(self):
        if self.edges is None:
            self.create_edges()

        assert self.is_boundary_edge is not None

        self._is_boundary_node = numpy.zeros(len(self.node_coords), dtype=bool)
        self._is_boundary_node[self.idx_hierarchy[..., self.is_boundary_edge]] = True

        self._is_interior_node = self.node_is_used & ~self.is_boundary_node

        self._is_boundary_facet = self.is_boundary_edge
        return

    @property
    def is_boundary_node(self):
        if self._is_boundary_node is None:
            self.mark_boundary()
        return self._is_boundary_node

    @property
    def is_interior_node(self):
        if self._is_interior_node is None:
            self.mark_boundary()
        return self._is_interior_node

    @property
    def is_boundary_facet(self):
        if self._is_boundary_facet is None:
            self.mark_boundary()
        return self._is_boundary_facet

    def create_edges(self):
        """Set up edge-node and edge-cell relations.
        """
        # Reshape into individual edges.
        # Sort the columns to make it possible for `unique()` to identify
        # individual edges.
        s = self.idx_hierarchy.shape
        a = numpy.sort(self.idx_hierarchy.reshape(s[0], -1).T)
        a_unique, inv, cts = unique_rows(a)

        assert numpy.all(
            cts < 3
        ), "No edge has more than 2 cells. Are cells listed twice?"

        self.is_boundary_edge = (cts[inv] == 1).reshape(s[1:])

        self.is_boundary_edge_individual = cts == 1

        self.edges = {"nodes": a_unique}

        # cell->edges relationship
        self.cells["edges"] = inv.reshape(3, -1).T

        self._edges_cells = None
        self._edge_gid_to_edge_list = None

        # Store an index {boundary,interior}_edge -> edge_gid
        self._edge_to_edge_gid = [
            [],
            numpy.where(self.is_boundary_edge_individual)[0],
            numpy.where(~self.is_boundary_edge_individual)[0],
        ]
        return

    @property
    def edges_cells(self):
        if self._edges_cells is None:
            self._compute_edges_cells()
        return self._edges_cells

    def _compute_edges_cells(self):
        """This creates interior edge->cells relations. While it's not
        necessary for many applications, it sometimes does come in handy.
        """
        if self.edges is None:
            self.create_edges()

        num_edges = len(self.edges["nodes"])

        counts = numpy.zeros(num_edges, dtype=int)
        fastfunc.add.at(
            counts,
            self.cells["edges"],
            numpy.ones(self.cells["edges"].shape, dtype=int),
        )

        # <https://stackoverflow.com/a/50395231/353337>
        edges_flat = self.cells["edges"].flat
        idx_sort = numpy.argsort(edges_flat)
        idx_start, count = grp_start_len(edges_flat[idx_sort])
        res1 = idx_sort[idx_start[count == 1]][:, numpy.newaxis]
        idx = idx_start[count == 2]
        res2 = numpy.column_stack([idx_sort[idx], idx_sort[idx + 1]])
        self._edges_cells = [
            [],  # no edges with zero adjacent cells
            res1 // 3,
            res2 // 3,
        ]
        # self._edges_local = [
        #     [],  # no edges with zero adjacent cells
        #     res1 % 3,
        #     res2 % 3,
        #     ]

        # For each edge, store the number of adjacent cells plus the index into
        # the respective edge array.
        self._edge_gid_to_edge_list = numpy.empty((num_edges, 2), dtype=int)
        self._edge_gid_to_edge_list[:, 0] = count
        c1 = count == 1
        l1 = numpy.sum(c1)
        self._edge_gid_to_edge_list[c1, 1] = numpy.arange(l1)
        c2 = count == 2
        l2 = numpy.sum(c2)
        self._edge_gid_to_edge_list[c2, 1] = numpy.arange(l2)
        assert l1 + l2 == len(count)

        return

    @property
    def edge_gid_to_edge_list(self):
        if self._edge_gid_to_edge_list is None:
            self._compute_edges_cells()
        return self._edge_gid_to_edge_list

    @property
    def face_partitions(self):
        # face = edge for triangles.
        # The partition is simply along the center of the edge.
        edge_lengths = self.edge_lengths
        return numpy.array([0.5 * edge_lengths, 0.5 * edge_lengths])

    @property
    def cell_partitions(self):
        if self._cell_partitions is None:
            # Compute the control volumes. Note that
            #   0.5 * (0.5 * edge_length) * covolume
            # = 0.25 * edge_length**2 * ce_ratio_edge_ratio
            self._cell_partitions = 0.25 * self.ei_dot_ei * self.ce_ratios
        return self._cell_partitions

    @property
    def cell_circumcenters(self):
        if self._cell_circumcenters is None:
            node_cells = self.cells["nodes"].T
            self._cell_circumcenters = compute_triangle_circumcenters(
                self.node_coords[node_cells], self.ei_dot_ei, self.ei_dot_ej
            )
        return self._cell_circumcenters

    @property
    def cell_centroids(self):
        """Computes the centroids (barycenters) of all triangles.
        """
        if self._cell_centroids is None:
            self._cell_centroids = (
                numpy.sum(self.node_coords[self.cells["nodes"]], axis=1) / 3.0
            )
        return self._cell_centroids

    @property
    def cell_barycenters(self):
        return self.cell_centroids

    @property
    def inradius(self):
        # See <http://mathworld.wolfram.com/Incircle.html>.
        abc = numpy.sqrt(self.ei_dot_ei)
        return 2 * self.cell_volumes / numpy.sum(abc, axis=0)

    @property
    def circumradius(self):
        # See <http://mathworld.wolfram.com/Incircle.html>.
        a, b, c = numpy.sqrt(self.ei_dot_ei)
        return (a * b * c) / numpy.sqrt(
            (a + b + c) * (-a + b + c) * (a - b + c) * (a + b - c)
        )

    @property
    def triangle_quality(self):
        # q = 2 * r_in / r_out
        #   = (-a+b+c) * (+a-b+c) * (+a+b-c) / (a*b*c),
        #
        # where r_in is the incircle radius and r_out the circumcircle radius
        # and a, b, c are the edge lengths.
        a, b, c = numpy.sqrt(self.ei_dot_ei)
        return (-a + b + c) * (a - b + c) * (a + b - c) / (a * b * c)

    @property
    def angles(self):
        # The cosines of the angles are the negative dot products of the normalized
        # edges adjacent to the angle.
        norms = numpy.sqrt(self.ei_dot_ei)
        normalized_ei_dot_ej = numpy.array(
            [
                self.ei_dot_ej[0] / norms[1] / norms[2],
                self.ei_dot_ej[1] / norms[2] / norms[0],
                self.ei_dot_ej[2] / norms[0] / norms[1],
            ]
        )
        return numpy.arccos(-normalized_ei_dot_ej)

    def _compute_integral_x(self):
        """Computes the integral of x,

          \int_V x,

        over all atomic "triangles", i.e., areas cornered by a node, an edge
        midpoint, and a circumcenter.
        """
        # The integral of any linear function over a triangle is the average of
        # the values of the function in each of the three corners, times the
        # area of the triangle.
        right_triangle_vols = self.cell_partitions

        node_edges = self.idx_hierarchy

        corner = self.node_coords[node_edges]
        edge_midpoints = 0.5 * (corner[0] + corner[1])
        cc = self.cell_circumcenters

        average = (corner + edge_midpoints[None] + cc[None, None]) / 3.0

        contribs = right_triangle_vols[None, :, :, None] * average

        return node_edges, contribs

    def _compute_surface_areas(self, cell_ids):
        """For each edge, one half of the the edge goes to each of the end
        points. Used for Neumann boundary conditions if on the boundary of the
        mesh and transition conditions if in the interior.
        """
        # Each of the three edges may contribute to the surface areas of all
        # three vertices. Here, only the two adjacent nodes receive a
        # contribution, but other approaches (e.g., the flat cell corrector),
        # may contribute to all three nodes.
        cn = self.cells["nodes"][cell_ids]
        ids = numpy.stack([cn, cn, cn], axis=1)

        half_el = 0.5 * self.edge_lengths[..., cell_ids]
        zero = numpy.zeros([half_el.shape[1]])
        vals = numpy.stack(
            [
                numpy.column_stack([zero, half_el[0], half_el[0]]),
                numpy.column_stack([half_el[1], zero, half_el[1]]),
                numpy.column_stack([half_el[2], half_el[2], zero]),
            ],
            axis=1,
        )

        return ids, vals

    #     def compute_gradient(self, u):
    #         '''Computes an approximation to the gradient :math:`\\nabla u` of a
    #         given scalar valued function :math:`u`, defined in the node points.
    #         This is taken from :cite:`NME2187`,
    #
    #            Discrete gradient method in solid mechanics,
    #            Lu, Jia and Qian, Jing and Han, Weimin,
    #            International Journal for Numerical Methods in Engineering,
    #            https://doi.org/10.1002/nme.2187.
    #         '''
    #         if self.cell_circumcenters is None:
    #             X = self.node_coords[self.cells['nodes']]
    #             self.cell_circumcenters = self.compute_triangle_circumcenters(X)
    #
    #         if 'cells' not in self.edges:
    #             self.edges['cells'] = self.compute_edge_cells()
    #
    #         # This only works for flat meshes.
    #         assert (abs(self.node_coords[:, 2]) < 1.0e-10).all()
    #         node_coords2d = self.node_coords[:, :2]
    #         cell_circumcenters2d = self.cell_circumcenters[:, :2]
    #
    #         num_nodes = len(node_coords2d)
    #         assert len(u) == num_nodes
    #
    #         gradient = numpy.zeros((num_nodes, 2), dtype=u.dtype)
    #
    #         # Create an empty 2x2 matrix for the boundary nodes to hold the
    #         # edge correction ((17) in [1]).
    #         boundary_matrices = {}
    #         for node in self.get_vertices('boundary'):
    #             boundary_matrices[node] = numpy.zeros((2, 2))
    #
    #         for edge_gid, edge in enumerate(self.edges['cells']):
    #             # Compute edge length.
    #             node0 = self.edges['nodes'][edge_gid][0]
    #             node1 = self.edges['nodes'][edge_gid][1]
    #
    #             # Compute coedge length.
    #             if len(self.edges['cells'][edge_gid]) == 1:
    #                 # Boundary edge.
    #                 edge_midpoint = 0.5 * (
    #                         node_coords2d[node0] +
    #                         node_coords2d[node1]
    #                         )
    #                 cell0 = self.edges['cells'][edge_gid][0]
    #                 coedge_midpoint = 0.5 * (
    #                         cell_circumcenters2d[cell0] +
    #                         edge_midpoint
    #                         )
    #             elif len(self.edges['cells'][edge_gid]) == 2:
    #                 cell0 = self.edges['cells'][edge_gid][0]
    #                 cell1 = self.edges['cells'][edge_gid][1]
    #                 # Interior edge.
    #                 coedge_midpoint = 0.5 * (
    #                         cell_circumcenters2d[cell0] +
    #                         cell_circumcenters2d[cell1]
    #                         )
    #             else:
    #                 raise RuntimeError(
    #                         'Edge needs to have either one or two neighbors.'
    #                         )
    #
    #             # Compute the coefficient r for both contributions
    #             coeffs = self.ce_ratios[edge_gid] / \
    #                 self.control_volumes[self.edges['nodes'][edge_gid]]
    #
    #             # Compute R*_{IJ} ((11) in [1]).
    #             r0 = (coedge_midpoint - node_coords2d[node0]) * coeffs[0]
    #             r1 = (coedge_midpoint - node_coords2d[node1]) * coeffs[1]
    #
    #             diff = u[node1] - u[node0]
    #
    #             gradient[node0] += r0 * diff
    #             gradient[node1] -= r1 * diff
    #
    #             # Store the boundary correction matrices.
    #             edge_coords = node_coords2d[node1] - node_coords2d[node0]
    #             if node0 in boundary_matrices:
    #                 boundary_matrices[node0] += numpy.outer(r0, edge_coords)
    #             if node1 in boundary_matrices:
    #                 boundary_matrices[node1] += numpy.outer(r1, -edge_coords)
    #
    #         # Apply corrections to the gradients on the boundary.
    #         for k, value in boundary_matrices.items():
    #             gradient[k] = numpy.linalg.solve(value, gradient[k])
    #
    #         return gradient

    def compute_curl(self, vector_field):
        """Computes the curl of a vector field over the mesh. While the vector
        field is point-based, the curl will be cell-based. The approximation is
        based on

        .. math::
            n\cdot curl(F) = \lim_{A\\to 0} |A|^{-1} <\int_{dGamma}, F> dr;

        see <https://en.wikipedia.org/wiki/Curl_(mathematics)>. Actually, to
        approximate the integral, one would only need the projection of the
        vector field onto the edges at the midpoint of the edges.
        """
        # Compute the projection of A on the edge at each edge midpoint.
        # Take the average of `vector_field` at the endpoints to get the
        # approximate value at the edge midpoint.
        A = 0.5 * numpy.sum(vector_field[self.idx_hierarchy], axis=0)
        # sum of <edge, A> for all three edges
        sum_edge_dot_A = numpy.einsum("ijk, ijk->j", self.half_edge_coords, A)

        # Get normalized vector orthogonal to triangle
        z = numpy.cross(self.half_edge_coords[0], self.half_edge_coords[1])

        # Now compute
        #
        #    curl = z / ||z|| * sum_edge_dot_A / |A|.
        #
        # Since ||z|| = 2*|A|, one can save a sqrt and do
        #
        #    curl = z * sum_edge_dot_A * 0.5 / |A|^2.
        #
        curl = z * (0.5 * sum_edge_dot_A / self.cell_volumes ** 2)[..., None]
        return curl

    def num_delaunay_violations(self):
        # Delaunay violations are present exactly on the interior edges where
        # the ce_ratio is negative. Count those.
        return numpy.sum(self.ce_ratios_per_interior_edge < 0.0)

    def show(self, *args, **kwargs):
        from matplotlib import pyplot as plt

        self.plot(*args, **kwargs)
        plt.show()
        plt.close()
        return

    def save(self, filename, *args, **kwargs):
        _, file_extension = os.path.splitext(filename)
        if file_extension == ".png":
            from matplotlib import pyplot as plt

            self.plot(*args, **kwargs)
            plt.savefig(filename, transparent=True, bbox_inches="tight")
            plt.close()
        else:
            self.write(filename)
        return

    def plot(
        self,
        show_coedges=True,
        show_centroids=True,
        mesh_color="k",
        nondelaunay_edge_color="#d62728",  # mpl 2.0 default red
        boundary_edge_color=None,
        comesh_color=(0.8, 0.8, 0.8),
        show_axes=True,
    ):
        """Show the mesh using matplotlib.
        """
        # Importing matplotlib takes a while, so don't do that at the header.
        from matplotlib import pyplot as plt
        from matplotlib.collections import LineCollection

        # from mpl_toolkits.mplot3d import Axes3D
        fig = plt.figure()
        # ax = fig.gca(projection='3d')
        ax = fig.gca()
        plt.axis("equal")
        if not show_axes:
            ax.set_axis_off()

        xmin = numpy.amin(self.node_coords[:, 0])
        xmax = numpy.amax(self.node_coords[:, 0])
        ymin = numpy.amin(self.node_coords[:, 1])
        ymax = numpy.amax(self.node_coords[:, 1])

        width = xmax - xmin
        xmin -= 0.1 * width
        xmax += 0.1 * width

        height = ymax - ymin
        ymin -= 0.1 * height
        ymax += 0.1 * height

        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)

        if self.edges is None:
            self.create_edges()

        # Get edges, cut off z-component.
        e = self.node_coords[self.edges["nodes"]][:, :, :2]
        # Plot regular edges, mark those with negative ce-ratio red.
        ce_ratios = self.ce_ratios_per_interior_edge
        pos = ce_ratios >= 0

        is_pos = numpy.zeros(len(self.edges["nodes"]), dtype=bool)
        is_pos[self._edge_to_edge_gid[2][pos]] = True

        # Mark Delaunay-conforming boundary edges
        is_pos_boundary = self.ce_ratios[self.is_boundary_edge] >= 0
        is_pos[self._edge_to_edge_gid[1][is_pos_boundary]] = True

        line_segments0 = LineCollection(e[is_pos], color=mesh_color)
        ax.add_collection(line_segments0)
        #
        line_segments1 = LineCollection(e[~is_pos], color=nondelaunay_edge_color)
        ax.add_collection(line_segments1)

        if show_coedges:
            # Connect all cell circumcenters with the edge midpoints
            cc = self.cell_circumcenters

            edge_midpoints = 0.5 * (
                self.node_coords[self.edges["nodes"][:, 0]]
                + self.node_coords[self.edges["nodes"][:, 1]]
            )

            # Plot connection of the circumcenter to the midpoint of all three
            # axes.
            a = numpy.stack(
                [cc[:, :2], edge_midpoints[self.cells["edges"][:, 0], :2]], axis=1
            )
            b = numpy.stack(
                [cc[:, :2], edge_midpoints[self.cells["edges"][:, 1], :2]], axis=1
            )
            c = numpy.stack(
                [cc[:, :2], edge_midpoints[self.cells["edges"][:, 2], :2]], axis=1
            )

            line_segments = LineCollection(
                numpy.concatenate([a, b, c]), color=comesh_color
            )
            ax.add_collection(line_segments)

        if boundary_edge_color:
            e = self.node_coords[self.edges["nodes"][self.is_boundary_edge_individual]][
                :, :, :2
            ]
            line_segments1 = LineCollection(e, color=boundary_edge_color)
            ax.add_collection(line_segments1)

        if show_centroids:
            centroids = self.control_volume_centroids
            ax.plot(
                centroids[:, 0],
                centroids[:, 1],
                linestyle="",
                marker=".",
                color="#d62728",
            )

        return fig

    def show_vertex(self, node_id, show_ce_ratio=True):
        """Plot the vicinity of a node and its ce_ratio.

        :param node_id: Node ID of the node to be shown.
        :type node_id: int

        :param show_ce_ratio: If true, shows the ce_ratio of the node, too.
        :type show_ce_ratio: bool, optional
        """
        # Importing matplotlib takes a while, so don't do that at the header.
        from matplotlib import pyplot as plt

        fig = plt.figure()
        ax = fig.gca()
        plt.axis("equal")

        # Find the edges that contain the vertex
        edge_gids = numpy.where((self.edges["nodes"] == node_id).any(axis=1))[0]
        # ... and plot them
        for node_ids in self.edges["nodes"][edge_gids]:
            x = self.node_coords[node_ids]
            ax.plot(x[:, 0], x[:, 1], "k")

        # Highlight ce_ratios.
        if show_ce_ratio:
            if self.cell_circumcenters is None:
                X = self.node_coords[self.cells["nodes"]]
                self.cell_circumcenters = self.compute_triangle_circumcenters(
                    X, self.ei_dot_ei, self.ei_dot_ej
                )

            # Find the cells that contain the vertex
            cell_ids = numpy.where((self.cells["nodes"] == node_id).any(axis=1))[0]

            for cell_id in cell_ids:
                for edge_gid in self.cells["edges"][cell_id]:
                    if node_id not in self.edges["nodes"][edge_gid]:
                        continue
                    node_ids = self.edges["nodes"][edge_gid]
                    edge_midpoint = 0.5 * (
                        self.node_coords[node_ids[0]] + self.node_coords[node_ids[1]]
                    )
                    p = _column_stack(self.cell_circumcenters[cell_id], edge_midpoint)
                    q = numpy.column_stack(
                        [
                            self.cell_circumcenters[cell_id],
                            edge_midpoint,
                            self.node_coords[node_id],
                        ]
                    )
                    ax.fill(q[0], q[1], color="0.5")
                    ax.plot(p[0], p[1], color="0.7")
        return

    def flip_until_delaunay(self):
        # If all coedge/edge ratios are positive, all cells are Delaunay.
        if numpy.all(self.ce_ratios > 0):
            return False

        # If all _interior_ coedge/edge ratios are positive, all cells are
        # Delaunay.
        if self.is_boundary_edge is None:
            self.create_edges()
        self.mark_boundary()
        if numpy.all(self.ce_ratios[~self.is_boundary_edge] > 0):
            return False

        if self._edges_cells is None:
            self._compute_edges_cells()

        num_flip_steps = 0
        ce_ratios_per_interior_edge = self.ce_ratios_per_interior_edge
        while numpy.any(ce_ratios_per_interior_edge < 0.0):
            num_flip_steps += 1

            is_flip_interior_edge = ce_ratios_per_interior_edge < 0.0

            interior_edges_cells = self._edges_cells[2]
            adj_cells = interior_edges_cells[is_flip_interior_edge].T

            # Check if there are cells for which more than one edge needs to be flipped.
            # For those, only flip one edge, namely that with the smaller (more
            # negative) ce_ratio.
            cell_gids, num_flips_per_cell = numpy.unique(adj_cells, return_counts=True)
            critical_cell_gids = cell_gids[num_flips_per_cell > 1]
            for cell_gid in critical_cell_gids:
                edge_gids = self.cells["edges"][cell_gid]
                num_adj_cells, edge_id = self._edge_gid_to_edge_list[edge_gids].T
                edge_ids = edge_id[num_adj_cells == 2]
                k = numpy.argmin(ce_ratios_per_interior_edge[edge_ids])
                is_flip_interior_edge[edge_ids] = False
                is_flip_interior_edge[edge_ids[k]] = True

            self.flip_interior_edges(is_flip_interior_edge)
            ce_ratios_per_interior_edge = self.ce_ratios_per_interior_edge

        return num_flip_steps > 1

    def flip_interior_edges(self, is_flip_interior_edge):
        if self._edges_cells is None:
            self._compute_edges_cells()

        interior_edges_cells = self._edges_cells[2]
        adj_cells = interior_edges_cells[is_flip_interior_edge].T

        edge_gids = self._edge_to_edge_gid[2][is_flip_interior_edge]
        adj_cells = interior_edges_cells[is_flip_interior_edge].T

        # Get the local ids of the edge in the two adjacent cells.
        # Get all edges of the adjacent cells
        ec = self.cells["edges"][adj_cells]
        # Find where the edge sits.
        hits = ec == edge_gids[None, :, None]
        # Make sure that there is exactly one match per cell
        assert numpy.all(numpy.sum(hits, axis=2) == 1)
        # translate to lids
        idx = numpy.empty(hits.shape, dtype=int)
        idx[..., 0] = 0
        idx[..., 1] = 1
        idx[..., 2] = 2
        lids = idx[hits].reshape((2, -1))

        #        3                   3
        #        A                   A
        #       /|\                 / \
        #      / | \               /   \
        #     /  |  \             /  1  \
        #   0/ 0 |   \1   ==>   0/_______\1
        #    \   | 1 /           \       /
        #     \  |  /             \  0  /
        #      \ | /               \   /
        #       \|/                 \ /
        #        V                   V
        #        2                   2
        #
        verts = numpy.array(
            [
                self.cells["nodes"][adj_cells[0], lids[0]],
                self.cells["nodes"][adj_cells[1], lids[1]],
                self.cells["nodes"][adj_cells[0], (lids[0] + 1) % 3],
                self.cells["nodes"][adj_cells[0], (lids[0] + 2) % 3],
            ]
        )

        self.edges["nodes"][edge_gids] = numpy.sort(verts[[0, 1]].T, axis=1)
        # No need to touch self.is_boundary_edge,
        # self.is_boundary_edge_individual; we're only flipping interior edges.

        # Do the neighboring cells have equal orientation (both node sets
        # clockwise/counterclockwise?
        equal_orientation = (
            self.cells["nodes"][adj_cells[0], (lids[0] + 1) % 3]
            == self.cells["nodes"][adj_cells[1], (lids[1] + 2) % 3]
        )

        # Set new cells
        self.cells["nodes"][adj_cells[0]] = verts[[0, 1, 2]].T
        self.cells["nodes"][adj_cells[1]] = verts[[0, 1, 3]].T

        # Set up new cells->edges relationships.
        previous_edges = self.cells["edges"][adj_cells].copy()

        i0 = numpy.ones(equal_orientation.shape[0], dtype=int)
        i0[~equal_orientation] = 2
        i1 = numpy.ones(equal_orientation.shape[0], dtype=int)
        i1[equal_orientation] = 2

        self.cells["edges"][adj_cells[0]] = numpy.column_stack(
            [
                numpy.choose((lids[1] + i0) % 3, previous_edges[1].T),
                numpy.choose((lids[0] + 2) % 3, previous_edges[0].T),
                edge_gids,
            ]
        )
        self.cells["edges"][adj_cells[1]] = numpy.column_stack(
            [
                numpy.choose((lids[1] + i1) % 3, previous_edges[1].T),
                numpy.choose((lids[0] + 1) % 3, previous_edges[0].T),
                edge_gids,
            ]
        )

        # update is_boundary_edge
        for k in range(3):
            self.is_boundary_edge[k, adj_cells] = self.is_boundary_edge_individual[
                self.cells["edges"][adj_cells, k]
            ]

        # Update the edge->cells relationship. It doesn't change for the edge that was
        # flipped, but for two of the other edges.
        confs = [
            (0, 1, numpy.choose((lids[0] + 1) % 3, previous_edges[0].T)),
            (1, 0, numpy.choose((lids[1] + i0) % 3, previous_edges[1].T)),
        ]
        for conf in confs:
            c, d, edge_gids = conf
            num_adj_cells, edge_id = self._edge_gid_to_edge_list[edge_gids].T

            k1 = num_adj_cells == 1
            k2 = num_adj_cells == 2
            assert numpy.all(numpy.logical_xor(k1, k2))

            # outer boundary edges
            edge_id1 = edge_id[k1]
            assert numpy.all(self._edges_cells[1][edge_id1][:, 0] == adj_cells[c, k1])
            self._edges_cells[1][edge_id1, 0] = adj_cells[d, k1]

            # interior edges
            edge_id2 = edge_id[k2]
            is_column0 = self._edges_cells[2][edge_id2][:, 0] == adj_cells[c, k2]
            is_column1 = self._edges_cells[2][edge_id2][:, 1] == adj_cells[c, k2]
            assert numpy.all(numpy.logical_xor(is_column0, is_column1))
            #
            self._edges_cells[2][edge_id2[is_column0], 0] = adj_cells[d, k2][is_column0]
            self._edges_cells[2][edge_id2[is_column1], 1] = adj_cells[d, k2][is_column1]

        # Schedule the cell ids for updates.
        update_cell_ids = numpy.unique(adj_cells.T.flat)
        # Same for edge ids
        k, edge_gids = self._edge_gid_to_edge_list[
            self.cells["edges"][update_cell_ids].flat
        ].T
        update_interior_edge_ids = numpy.unique(edge_gids[k == 2])

        self._update_cell_values(update_cell_ids, update_interior_edge_ids)
        return

    def _update_cell_values(self, cell_ids, interior_edge_ids):
        """Updates all sorts of cell information for the given cell IDs.
        """
        # update idx_hierarchy
        nds = self.cells["nodes"][cell_ids].T
        self.idx_hierarchy[..., cell_ids] = nds[self.local_idx]

        # update self.half_edge_coords
        self.half_edge_coords[:, cell_ids, :] = numpy.moveaxis(
            self.node_coords[self.idx_hierarchy[1, ..., cell_ids]]
            - self.node_coords[self.idx_hierarchy[0, ..., cell_ids]],
            0,
            1,
        )

        # update self.ei_dot_ej
        self.ei_dot_ej[:, cell_ids] = numpy.einsum(
            "ijk, ijk->ij",
            self.half_edge_coords[[1, 2, 0]][:, cell_ids],
            self.half_edge_coords[[2, 0, 1]][:, cell_ids],
        )

        # update self.ei_dot_ei
        e = self.half_edge_coords[:, cell_ids]
        self.ei_dot_ei[:, cell_ids] = numpy.einsum("ijk, ijk->ij", e, e)

        # update cell_volumes, ce_ratios_per_half_edge
        cv, ce = compute_tri_areas_and_ce_ratios(self.ei_dot_ej[:, cell_ids])
        self.cell_volumes[cell_ids] = cv
        self.ce_ratios[:, cell_ids] = ce

        if self._interior_ce_ratios is not None:
            self._interior_ce_ratios[interior_edge_ids] = 0.0
            edge_gids = self._edge_to_edge_gid[2][interior_edge_ids]
            adj_cells = self._edges_cells[2][interior_edge_ids]

            is0 = self.cells["edges"][adj_cells[:, 0]][:, 0] == edge_gids
            is1 = self.cells["edges"][adj_cells[:, 0]][:, 1] == edge_gids
            is2 = self.cells["edges"][adj_cells[:, 0]][:, 2] == edge_gids
            assert numpy.all(
                numpy.sum(numpy.column_stack([is0, is1, is2]), axis=1) == 1
            )
            #
            self._interior_ce_ratios[interior_edge_ids[is0]] += self.ce_ratios[
                0, adj_cells[is0, 0]
            ]
            self._interior_ce_ratios[interior_edge_ids[is1]] += self.ce_ratios[
                1, adj_cells[is1, 0]
            ]
            self._interior_ce_ratios[interior_edge_ids[is2]] += self.ce_ratios[
                2, adj_cells[is2, 0]
            ]

            is0 = self.cells["edges"][adj_cells[:, 1]][:, 0] == edge_gids
            is1 = self.cells["edges"][adj_cells[:, 1]][:, 1] == edge_gids
            is2 = self.cells["edges"][adj_cells[:, 1]][:, 2] == edge_gids
            assert numpy.all(
                numpy.sum(numpy.column_stack([is0, is1, is2]), axis=1) == 1
            )
            #
            self._interior_ce_ratios[interior_edge_ids[is0]] += self.ce_ratios[
                0, adj_cells[is0, 1]
            ]
            self._interior_ce_ratios[interior_edge_ids[is1]] += self.ce_ratios[
                1, adj_cells[is1, 1]
            ]
            self._interior_ce_ratios[interior_edge_ids[is2]] += self.ce_ratios[
                2, adj_cells[is2, 1]
            ]

        if self._signed_cell_areas is not None:
            # One could make p contiguous by adding a copy(), but that's not
            # really worth it here.
            p = self.node_coords[self.cells["nodes"][cell_ids]].T
            # <https://stackoverflow.com/q/50411583/353337>
            self._signed_cell_areas[cell_ids] = (
                +p[0][2] * (p[1][0] - p[1][1])
                + p[0][0] * (p[1][1] - p[1][2])
                + p[0][1] * (p[1][2] - p[1][0])
            ) / 2

        # TODO update those values
        self._cell_centroids = None
        self._edge_lengths = None
        self._cell_circumcenters = None
        self._control_volumes = None
        self._cell_partitions = None
        self._cv_centroids = None
        self._surface_areas = None
        self.subdomains = {}
        return
