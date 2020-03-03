import logging
from collections import defaultdict
import os
from scipy.spatial import cKDTree as KDTree
import numpy as np
from scipy.sparse import dok_matrix
import scipy.sparse.csgraph as csgraph
from pathlib import Path
from .cif import Cif
from .unit_cell import UnitCell
from .space_group import SpaceGroup, SymmetryOperation
from .element import Element, chemical_formula
from .molecule import Molecule
from .util import cartesian_product


LOG = logging.getLogger(__name__)

# TODO add LinearSegmentedColormap objects for other
# CrystalExplorer default colors
DEFAULT_COLORMAPS = {
    "d_norm": "bwr_r",
    "d_e": "viridis_r",
    "d_i": "viridis_r",
    "d_norm_i": "viridis_r",
    "d_norm_e": "viridis_r",
}


class AsymmetricUnit:
    """Storage class for the coordinates and labels in a crystal
    asymmetric unit
    Create an asymmetric unit object from a list of Elements and 
    an array of fractional coordinates.

    Parameters
    ----------
    elements : :obj:`list` of :obj:`Element`
        N length list of elements associated with the sites in this asymmetric
        unit
    positions : array_like
        (N, 3) array of site positions in fractional coordinates
    labels : array_like
        N length array of string labels for each site
    **kwargs
        Additional properties (will populate the properties member)
        to store in this asymmetric unit
    """

    def __init__(self, elements, positions, labels=None, **kwargs):
        self.elements = elements
        self.atomic_numbers = np.asarray([x.atomic_number for x in elements])
        self.positions = np.asarray(positions)
        self.properties = {}
        self.properties.update(kwargs)
        if labels is None:
            self.labels = []
            label_index = defaultdict(int)
            for el in self.elements:
                label_index[el] += 1
                self.labels.append("{}{}".format(el, label_index[el]))
        else:
            self.labels = labels
        self.labels = np.array(self.labels)

    @property
    def formula(self):
        """Molecular formula for this asymmetric unit"""
        return chemical_formula(self.elements, subscript=False)

    def __len__(self):
        return len(self.elements)

    def __repr__(self):
        return "<{}>".format(self.formula)

    @classmethod
    def from_records(cls, records):
        """Initialize an AsymmetricUnit from a list of dictionary like objects
        
        Parameters
        ----------
        records : iterable
            An iterable containing dict_like objects with `label`,
            `element`, `position` and optionally `occupation` stored.
        """
        labels = []
        elements = []
        positions = []
        occupation = []
        for r in records:
            labels.append(r["label"])
            elements.append(Element[r["element"]])
            positions.append(r["position"])
            occupation.append(r.get("occupation", 1.0))
        positions = np.asarray(positions)
        return AsymmetricUnit(elements, positions, labels=labels, occupation=occupation)


class Crystal:
    """Storage class for a crystal structure, consisting of
    an asymmetric unit, a unit cell and space group information.

    Parameters
    ----------
    unit_cell : :obj:`UnitCell`
        The unit cell for this crystal i.e. the translational symmetry
        of the crystal structure.
    space_group : :obj:`SpaceGroup`
        The space group symmetry of this crystal i.e. the generators
        for populating the unit cell given the asymmetric unit.
    asymmetric_unit : :obj:`AsymmetricUnit`
        The asymmetric unit of this crystal. The sites of this
        combined with the space group will generate all translationally
        equivalent positions.
    **kwargs
        Optional properties to (will populate the properties member) store
        about the the crystal structure.
    """

    space_group: SpaceGroup
    unit_cell: UnitCell
    asymmetric_unit: AsymmetricUnit
    properties: dict

    def __init__(self, unit_cell, space_group, asymmetric_unit, **kwargs):
        self.space_group = space_group
        self.unit_cell = unit_cell
        self.asymmetric_unit = asymmetric_unit
        self.properties = {}
        self.properties.update(kwargs)

    @property
    def site_positions(self):
        """Row major array of asymmetric unit atomic positions

        Returns
        -------
        array_like
            The positions in fractional coordinates of the asymmetric unit.
        """
        return self.asymmetric_unit.positions

    @property
    def site_atoms(self):
        """Array of asymmetric unit atomic numbers

        Returns
        -------
        array_like
            The atomic numbers of the asymmetric unit.
        """
        return self.asymmetric_unit.atomic_numbers

    @property
    def nsites(self):
        """The number of sites in the asymmetric unit.

        Returns
        -------
        int
            The number of sites in the asymmetric unit.
        """

        return len(self.site_atoms)

    @property
    def symmetry_operations(self):
        """Symmetry operations that generate this crystal.

        Returns
        -------
        list of :obj:`SymmetryOperation`
            List of SymmetryOperation objects belonging to the space group
            symmetry of this crystal.
        """
        return self.space_group.symmetry_operations

    def to_cartesian(self, coords):
        """Convert coordinates (row major) from fractional to cartesian coordinates.

        Parameters
        ----------
        coords : array_like
            (N, 3) array of positions assumed to be in fractional coordinates

        Returns
        -------
        array_like
            (N, 3) array of positions transformed to cartesian (orthogonal) coordinates
            by the unit cell of this crystal.
        """
        return self.unit_cell.to_cartesian(coords)

    def to_fractional(self, coords):
        """Convert coordinates (row major) from cartesian to fractional coordinates.

        Parameters
        ----------
        coords : array_like
            (N, 3) array of positions assumed to be in cartesian (orthogonal) coordinates

        Returns
        -------
        array_like
            (N, 3) array of positions transformed to fractional coordinates
            by the unit cell of this crystal.
        """

        return self.unit_cell.to_fractional(coords)

    def unit_cell_atoms(self, tolerance=1e-3):
        """Generate all atoms in the unit cell (i.e. with 
        fractional coordinates in [0, 1]) along with associated
        information about symmetry operations, occupation, elements
        related asymmetric_unit atom etc.
        
        Will merge atom sites within tolerance of each other, and
        sum their occupation numbers. A warning will be logged if
        any atom site in the unit cell has > 1.0 occupancy after
        this.

        Sets the `_unit_cell_atom_dict` member as this is an expensive
        operation and is worth caching the result. Subsequent calls
        to this function will be a no-op.

        Parameters
        ----------
        tolerance : float, optional
            Minimum separation of sites in the unit cell, below which 
            atoms/sites will be merged and their (partial) occupations
            added.

        Returns
        -------
        dict
            A dictionary of arrays associated with all sites contained
            in the unit cell of this crystal, members are:

            asym_atom: corresponding asymmetric unit atom indices for all sites.

            frac_pos: (N, 3) array of fractional positions for all sites.

            cart_pos: (N, 3) array of cartesian positions for all sites.

            element: (N) array of atomic numbers for all sites.

            symop: (N) array of indices corresponding to the generator symmetry
            operation for each site.

            label: (N) array of string labels corresponding to each site

            occupation: (N) array of occupation numbers for each site. Will
            warn if any of these are greater than 1.0

        """

        if hasattr(self, "_unit_cell_atom_dict"):
            return getattr(self, "_unit_cell_atom_dict")
        pos = self.site_positions
        atoms = self.site_atoms
        natom = self.nsites
        nsymops = len(self.space_group.symmetry_operations)
        occupation = np.tile(
            self.asymmetric_unit.properties.get("occupation", np.ones(natom)), nsymops
        )
        labels = np.tile(self.asymmetric_unit.labels, nsymops)
        uc_nums = np.tile(atoms, nsymops)
        asym = np.arange(len(uc_nums)) % natom
        sym, uc_pos = self.space_group.apply_all_symops(pos)
        translated = np.fmod(uc_pos + 7.0, 1)
        tree = KDTree(translated)
        dist = tree.sparse_distance_matrix(tree, max_distance=tolerance)
        mask = np.ones(len(uc_pos), dtype=bool)
        # because crystals may have partially occupied sites
        # on special positions, we need to merge some sites
        expected_natoms = np.sum(occupation)
        for (i, j), d in dist.items():
            if not (i < j):
                continue
            occupation[i] += occupation[j]
            mask[j] = False
        occupation = occupation[mask]
        if np.any(occupation > 1.0):
            LOG.debug("Some unit cell site occupations are > 1.0")
        setattr(
            self,
            "_unit_cell_atom_dict",
            {
                "asym_atom": asym[mask],
                "frac_pos": translated[mask],
                "element": uc_nums[mask],
                "symop": sym[mask],
                "label": labels[mask],
                "occupation": occupation,
                "cart_pos": self.to_cartesian(translated[mask]),
            },
        )
        return self._unit_cell_atom_dict

    def unit_cell_connectivity(self, tolerance=0.4, neighbouring_cells=1):
        """Periodic connectiviy for the unit cell, populates _uc_graph
        with a networkx.Graph object, where nodes are indices into the
        _unit_cell_atom_dict arrays and the edges contain the translation
        (cell) for the image of the corresponding unit cell atom with the
        higher index to be bonded to the lower

        Bonding is determined by interatomic distances being less than the
        sum of covalent radii for the sites plus the tolerance (provided 
        as a parameter)
        
        Parameters
        ----------
        tolerance : float, optional
            Bonding tolerance (bonded if d < cov_a + cov_b + tolerance)
        neighbouring_cells : int, optional
            Number of neighbouring cells in which to look for bonded atoms.
            We start at the (0, 0, 0) cell, so a value of 1 will look in the
            (0, 0, 1), (0, 1, 1), (1, 1, 1) i.e. all 26 neighbouring cells.
            1 is typically sufficient for organic systems.

        Returns
        -------
        :obj:`tuple` of (sparse_matrix in dict of keys format, dict)
            the (i, j) value in this matrix is the bond length from i,j
            the (i, j) value in the dict is the cell translation on j which
            bonds these two sites
        """

        if hasattr(self, "_uc_graph"):
            return getattr(self, "_uc_graph")
        slab = self.slab(bounds=((-1, -1, -1), (1, 1, 1)))
        n_uc = slab["n_uc"]
        uc_pos = slab["frac_pos"][:n_uc]
        uc_nums = slab["element"][:n_uc]
        neighbour_pos = slab["frac_pos"][n_uc:]
        cart_uc_pos = self.to_cartesian(uc_pos)
        unique_elements = {x: Element.from_atomic_number(x) for x in np.unique(uc_nums)}
        # first establish all connections in the unit cell
        covalent_radii = np.array([unique_elements[x].cov for x in uc_nums])
        max_cov = np.max(covalent_radii)
        # TODO this needs to be sped up for large cells, tends to slow for > 1000 atoms
        # and the space storage will become a problem
        thresholds = (
            covalent_radii[:, np.newaxis] + covalent_radii[np.newaxis, :] + tolerance
        )
        tree = KDTree(cart_uc_pos)
        dist = tree.sparse_distance_matrix(tree, max_distance=2 * max_cov + tolerance)
        uc_edges = []

        for (i, j), d in dist.items():
            if not (i < j):
                continue
            if d > 1e-3 and d < (covalent_radii[i] + covalent_radii[j] + tolerance):
                uc_edges.append((i, j, d, (0, 0, 0)))

        idxs = np.arange(n_uc)
        asymmetric_unit_idx = slab["asym_atom"]
        cart_neighbour_pos = self.unit_cell.to_cartesian(neighbour_pos)
        tree2 = KDTree(cart_neighbour_pos)
        dist = tree.sparse_distance_matrix(tree2, max_distance=2 * max_cov + tolerance)
        # could be sped up if done outside python
        cells = slab["cell"][n_uc:]
        for (uc_atom, neighbour_atom), d in dist.items():
            uc_idx = neighbour_atom % n_uc
            if not (uc_atom < uc_idx):
                continue
            if d > 1e-3 and d < (
                covalent_radii[uc_atom] + covalent_radii[uc_idx] + tolerance
            ):
                cell = cells[neighbour_atom]
                uc_edges.append((uc_atom, uc_idx, d, tuple(cell)))

        properties = {}
        uc_graph = dok_matrix((n_uc, n_uc))
        for i, j, d, cell in uc_edges:
            uc_graph[i, j] = d
            properties[(i, j)] = cell

        setattr(self, "_uc_graph", (uc_graph, properties))
        return self._uc_graph

    def unit_cell_molecules(self):
        """Calculate the molecules for all sites in the unit cell,
        where the number of molecules will be equal to number of
        symmetry unique molecules times number of symmetry operations.
        
        Returns
        -------
        list of :obj:`Molecule`
            List of all connected molecules in this crystal, which
            when translated by the unit cell would produce the full crystal.
            If the asymmetric is molecular, the list will be of length
            num_molecules_in_asymmetric_unit * num_symm_operations
        """

        if hasattr(self, "_unit_cell_molecules"):
            return getattr(self, "_unit_cell_molecules")
        uc_graph, edge_cells = self.unit_cell_connectivity()
        n_uc_mols, uc_mols = csgraph.connected_components(
            csgraph=uc_graph, directed=False, return_labels=True
        )
        uc_frac = self._unit_cell_atom_dict["frac_pos"]
        uc_cartesian = self._unit_cell_atom_dict["cart_pos"]
        uc_elements = self._unit_cell_atom_dict["element"]
        uc_asym = self._unit_cell_atom_dict["asym_atom"]
        uc_symop = self._unit_cell_atom_dict["symop"]

        molecules = []

        n_uc = len(uc_frac)
        LOG.debug("%d molecules in unit cell", n_uc_mols)
        for i in range(n_uc_mols):
            nodes = np.where(uc_mols == i)[0]
            root = nodes[0]
            elements = uc_elements[nodes]
            shifts = np.zeros((n_uc, 3))
            ordered, pred = csgraph.breadth_first_order(
                csgraph=uc_graph, i_start=root, directed=False
            )
            for j in ordered[1:]:
                i = pred[j]
                if j < i:
                    shifts[j, :] = shifts[i, :] - edge_cells[(j, i)]
                else:
                    shifts[j, :] = shifts[i, :] + edge_cells[(i, j)]
            positions = self.to_cartesian((uc_frac + shifts)[nodes])
            asym_atoms = uc_asym[nodes]
            reorder = np.argsort(asym_atoms)
            asym_atoms = asym_atoms[reorder]

            mol = Molecule.from_arrays(
                elements=elements[reorder],
                positions=positions[reorder],
                guess_bonds=True,
                unit_cell_atoms=np.array(nodes)[reorder],
                asymmetric_unit_atoms=asym_atoms,
                asymmetric_unit_labels=self.asymmetric_unit.labels[asym_atoms],
                generator_symop=uc_symop[np.asarray(nodes)[reorder]],
            )
            molecules.append(mol)
        setattr(self, "_unit_cell_molecules", molecules)
        return molecules

    def symmetry_unique_molecules(self, bond_tolerance=0.4):
        """Calculate a list of connected molecules which contain
        every site in the asymmetric_unit

        Populates the _symmetry_unique_molecules member, subsequent
        calls to this function will be a no-op.
       
        Parameters
        ----------
        bond_tolerance : float, optional
            Bonding tolerance (bonded if d < cov_a + cov_b + bond_tolerance)

        Returns
        -------
        list of :obj:`Molecule`
            List of all connected molecules in the asymmetric_unit of this
            crystal, i.e. the minimum list of connected molecules which contain
            all sites in the asymmetric unit.
            If the asymmetric is molecular, the list will be of length
            num_molecules_in_asymmetric_unit and the total number of atoms
            will be equal to the number of atoms in the asymmetric_unit
        """

        if hasattr(self, "_symmetry_unique_molecules"):
            return getattr(self, "_symmetry_unique_molecules")
        uc_molecules = self.unit_cell_molecules()
        asym_atoms = np.zeros(len(self.asymmetric_unit), dtype=bool)
        molecules = []
        # sort by % of identity symop
        order = lambda x: len(np.where(x.asym_symops == 16484)[0]) / len(x)
        for i, mol in enumerate(sorted(uc_molecules, key=order, reverse=True)):
            asym_atoms_in_g = np.unique(mol.properties["asymmetric_unit_atoms"])
            if np.all(asym_atoms[asym_atoms_in_g]):
                continue
            asym_atoms[asym_atoms_in_g] = True
            molecules.append(mol)
            if np.all(asym_atoms):
                break
        LOG.debug("%d symmetry unique molecules", len(molecules))
        setattr(self, "_symmetry_unique_molecules", molecules)
        return molecules

    def slab(self, bounds=((-1, -1, -1), (1, 1, 1))):
        """Calculate the atoms and associated information
        for a slab consisting of multiple unit cells.

        If unit cell atoms have not been calculated, this calculates 
        their information and caches it.

        Parameters
        ----------
        bounds: tuple, optional
            Tuple of upper and lower corners (hkl) describing the bounds
            of the slab.
        
        Returns
        -------
        dict
            A dictionary of arrays associated with all sites contained
            in the unit cell of this crystal, members are:

            asym_atom: corresponding asymmetric unit atom indices for all sites.

            frac_pos: (N, 3) array of fractional positions for all sites.

            cart_pos: (N, 3) array of cartesian positions for all sites.

            element: (N) array of atomic numbers for all sites.

            symop: (N) array of indices corresponding to the generator symmetry
            operation for each site.

            label: (N) array of string labels corresponding to each site
            occupation: (N) array of occupation numbers for each site. Will
            warn if any of these are greater than 1.0

            cell: (N,3) array of cell indices for each site

            n_uc: number of atoms in the unit cell

            n_cells: number of cells in this slab

            occupation: (N) array of occupation numbers for each site. Will
            warn if any of these are greater than 1.0
    
        """
        uc_atoms = self.unit_cell_atoms()
        (hmin, kmin, lmin), (hmax, kmax, lmax) = bounds
        h = np.arange(hmin, hmax + 1)
        k = np.arange(kmin, kmax + 1)
        l = np.arange(lmin, lmax + 1)
        cells = cartesian_product(
            h[np.argsort(np.abs(h))], k[np.argsort(np.abs(k))], l[np.argsort(np.abs(l))]
        )
        ncells = len(cells)
        uc_pos = uc_atoms["frac_pos"]
        n_uc = len(uc_pos)
        pos = np.empty((ncells * n_uc, 3), dtype=np.float64)
        slab_cells = np.empty((ncells * n_uc, 3), dtype=np.float64)
        for i, cell in enumerate(cells):
            pos[i * n_uc : (i + 1) * n_uc, :] = uc_pos + cell
            slab_cells[i * n_uc : (i + 1) * n_uc] = cell
        slab_dict = {
            k: np.tile(v, ncells) for k, v in uc_atoms.items() if not k.endswith("pos")
        }
        slab_dict["frac_pos"] = pos
        slab_dict["cell"] = slab_cells
        slab_dict["n_uc"] = n_uc
        slab_dict["n_cells"] = ncells
        slab_dict["cart_pos"] = self.to_cartesian(pos)
        return slab_dict

    def atoms_in_radius(self, radius, origin=(0, 0, 0)):
        frac_origin = self.to_fractional(origin)
        frac_radius = radius / np.array(self.unit_cell.lengths)
        hmax, kmax, lmax = np.ceil(frac_radius + frac_origin).astype(int)
        hmin, kmin, lmin = np.floor(frac_origin - frac_radius).astype(int)
        slab = self.slab(bounds=((hmin, kmin, lmin), (hmax, kmax, lmax)))
        tree = KDTree(slab["cart_pos"])
        idxs = sorted(tree.query_ball_point(origin, radius))
        result = {k: v[idxs] for k, v in slab.items() if isinstance(v, np.ndarray)}
        result["uc_atom"] = np.tile(np.arange(slab["n_uc"]), slab["n_cells"])[idxs]
        return result

    def atomic_surroundings(self, radius=6.0):
        cart_asym = self.to_cartesian(self.asymmetric_unit.positions)
        hklmax = np.array([-np.inf, -np.inf, -np.inf])
        hklmin = np.array([np.inf, np.inf, np.inf])
        frac_radius = radius / np.array(self.unit_cell.lengths)
        for pos in self.asymmetric_unit.positions:
            hklmax = np.maximum(hklmax, np.ceil(frac_radius + pos))
            hklmin = np.minimum(hklmin, np.floor(pos - frac_radius))
        hmax, kmax, lmax = hklmax.astype(int)
        hmin, kmin, lmin = hklmin.astype(int)
        slab = self.slab(bounds=((hmin, kmin, lmin), (hmax, kmax, lmax)))
        tree = KDTree(slab["cart_pos"])
        results = []
        for i, (n, pos) in enumerate(zip(self.asymmetric_unit.elements, cart_asym)):
            idxs = tree.query_ball_point(pos, radius)
            positions = slab["cart_pos"][idxs]
            elements = slab["element"][idxs]
            d = np.linalg.norm(positions - pos, axis=1)
            keep = np.where(d > 1e-3)[0]
            results.append((n.atomic_number, pos, elements[keep], positions[keep]))
        return results

    def atom_group_surroundings(self, atoms, radius=6.0):
        results = []
        hklmax = np.array([-np.inf, -np.inf, -np.inf])
        hklmin = np.array([np.inf, np.inf, np.inf])
        frac_radius = radius / np.array(self.unit_cell.lengths)
        mol = self.symmetry_unique_molecules()[0]
        central_positions = self.to_fractional(mol.positions[atoms])
        central_elements = mol.atomic_numbers[atoms]
        central_cart_positions = mol.positions[atoms]

        for pos in central_positions:
            hklmax = np.maximum(hklmax, np.ceil(frac_radius + pos))
            hklmin = np.minimum(hklmin, np.floor(pos - frac_radius))
        hmax, kmax, lmax = hklmax.astype(int)
        hmin, kmin, lmin = hklmin.astype(int)
        slab = self.slab(bounds=((hmin, kmin, lmin), (hmax, kmax, lmax)))
        elements = slab["element"]
        positions = slab["cart_pos"]
        tree = KDTree(positions)
        keep = np.zeros(positions.shape[0], dtype=bool)

        this_mol = []
        for i, (n, pos) in enumerate(zip(central_elements, central_cart_positions)):
            idxs = tree.query_ball_point(pos, radius)
            d, nn = tree.query(pos)
            keep[idxs] = True
            if d < 1e-3:
                this_mol.append(nn)
                keep[this_mol] = False
        return (
            (central_elements, central_cart_positions),
            (elements[keep], positions[keep]),
        )

    def molecule_surroundings(self, radius=6.0):
        """Calculate the atomic information for all
        atoms surrounding each symmetry unique molecule
        in this crystal within the given radius.

        Parameters
        ----------
        radius: float, optional
            Maximum distance in Angstroms between any atom in the molecule
            and the resulting neighbouring atoms

        Returns
        -------
        list of tuple
            A list of tuples of (Molecule, elements, positions)
            where `elements` is an :obj:`np.ndarray` of atomic numbers,
            and `positions` is an :obj:`np.ndarray` of Cartesian atomic positions
        """
        results = []
        for mol in self.symmetry_unique_molecules():
            hklmax = np.array([-np.inf, -np.inf, -np.inf])
            hklmin = np.array([np.inf, np.inf, np.inf])
            frac_radius = radius / np.array(self.unit_cell.lengths)
            for pos in self.to_fractional(mol.positions):
                hklmax = np.maximum(hklmax, np.ceil(frac_radius + pos))
                hklmin = np.minimum(hklmin, np.floor(pos - frac_radius))
            hmax, kmax, lmax = hklmax.astype(int)
            hmin, kmin, lmin = hklmin.astype(int)
            slab = self.slab(bounds=((hmin, kmin, lmin), (hmax, kmax, lmax)))
            elements = slab["element"]
            positions = slab["cart_pos"]
            tree = KDTree(positions)
            keep = np.zeros(positions.shape[0], dtype=bool)
            this_mol = []
            for i, (n, pos) in enumerate(zip(mol.elements, mol.positions)):
                idxs = tree.query_ball_point(pos, radius)
                d, nn = tree.query(pos)
                keep[idxs] = True
                if d < 1e-3:
                    this_mol.append(nn)
                    keep[this_mol] = False
            results.append((mol, elements[keep], positions[keep]))
        return results

    def functional_group_surroundings(self, radius=6.0, kind="carboxylic_acid"):
        """Calculate the atomic information for all
        atoms surrounding each functional group in each symmetry unique molecule
        in this crystal within the given radius.

        Parameters
        ----------
        radius: float, optional
            Maximum distance in Angstroms between any atom in the molecule
            and the resulting neighbouring atoms

        Returns
        -------
        list of tuple
            A list of tuples of (func_el, func_pos, neigh_el, neigh_pos)
            where `func_el` and `neigh_el` are :obj:`np.ndarray` of atomic numbers,
            and `func_pos` and `neigh_pos` are :obj:`np.ndarray` of Cartesian atomic positions
        """
        results = []
        for mol in self.symmetry_unique_molecules():
            hklmax = np.array([-np.inf, -np.inf, -np.inf])
            hklmin = np.array([np.inf, np.inf, np.inf])
            frac_radius = radius / np.array(self.unit_cell.lengths)
            for pos in self.to_fractional(mol.positions):
                hklmax = np.maximum(hklmax, np.ceil(frac_radius + pos))
                hklmin = np.minimum(hklmin, np.floor(pos - frac_radius))
            hmax, kmax, lmax = hklmax.astype(int)
            hmin, kmin, lmin = hklmin.astype(int)
            slab = self.slab(bounds=((hmin, kmin, lmin), (hmax, kmax, lmax)))
            elements = slab["element"]
            positions = slab["cart_pos"]
            tree = KDTree(positions)
            groups = mol.functional_groups(kind=kind)
            for fg in groups:
                keep = np.zeros(positions.shape[0], dtype=bool)
                inside = []
                for i, (n, pos) in enumerate(
                    zip(mol.atomic_numbers[fg], mol.positions[fg])
                ):
                    idxs = tree.query_ball_point(pos, radius)
                    d, nn = tree.query(pos)
                    keep[idxs] = True
                    if d < 1e-3:
                        inside.append(nn)
                        keep[inside] = False
                results.append(
                    (
                        mol.atomic_numbers[fg],
                        mol.positions[fg],
                        elements[keep],
                        positions[keep],
                    )
                )
        return results

    def promolecule_density_isosurfaces(self, **kwargs):
        """Calculate promolecule electron density isosurfaces
        for each symmetry unique molecule in this crystal.

        Keyword Args
        ------------
        isovalue: float, optional
            level set value for the isosurface (default=0.002) in au.
        separation: float, optional
            separation between density grid used in the surface calculation
            (default 0.2) in Angstroms.
        color: str, optional
            surface property to use for vertex coloring, one of ('d_norm_i',
            'd_i', 'd_norm_e', 'd_e')
        colormap: str, optional
            matplotlib colormap to use for surface coloring (default 'viridis_r')
        midpoint: float, optional, default 0.0 if using d_norm
            use the midpoint norm (as is used in CrystalExplorer)

        Returns
        -------
        list of :obj:`trimesh.Trimesh`
            A list of meshes representing the promolecule density isosurfaces
        """
        from .density import PromoleculeDensity
        from .surface import promolecule_density_isosurface
        from matplotlib.cm import get_cmap
        import trimesh

        isovalue = kwargs.get("isovalue", 0.002)
        sep = kwargs.get("separation", kwargs.get("resolution", 0.2))
        vertex_color = kwargs.get("color", "d_norm_i")
        midpoint = kwargs.get("midpoint", 0.0 if vertex_color == "d_norm" else None)
        meshes = []
        colormap = get_cmap(
            kwargs.get("colormap", DEFAULT_COLORMAPS.get(vertex_color, "viridis_r"))
        )
        for mol in self.symmetry_unique_molecules():
            pro = PromoleculeDensity((mol.atomic_numbers, mol.positions))
            iso = promolecule_density_isosurface(pro, sep=sep, isovalue=isovalue)
            prop = iso.vertex_prop[vertex_color]
            norm = None
            if midpoint is not None:
                from matplotlib.colors import DivergingNorm

                norm = DivergingNorm(vmin=prop.min(), vcenter=midpoint, vmax=prop.max())
                prop = norm(prop)
            color = colormap(prop)
            mesh = trimesh.Trimesh(
                vertices=iso.vertices,
                faces=iso.faces,
                normals=iso.normals,
                vertex_colors=color,
            )
            meshes.append(mesh)
        return meshes

    def void_surface(self, **kwargs):
        from shmolecule.density import PromoleculeDensity
        import trimesh
        from .mc import marching_cubes
        from scipy.spatial import cKDTree as KDTree

        atoms = self.slab()
        density = PromoleculeDensity((atoms["element"], atoms["cart_pos"]))
        sep = kwargs.get("separation", kwargs.get("resolution", 0.5))
        isovalue = kwargs.get("isovalue", 5e-4)
        seps = sep / np.array(self.unit_cell.lengths)
        x_grid = np.arange(0, 1.0, seps[0], dtype=np.float32)
        y_grid = np.arange(0, 1.0, seps[1], dtype=np.float32)
        z_grid = np.arange(0, 1.0, seps[2], dtype=np.float32)
        x, y, z = np.meshgrid(x_grid, y_grid, z_grid)
        separations = np.array((sep, sep, sep))
        shape = x.shape
        pts = np.c_[x.ravel(), y.ravel(), z.ravel()]
        pts = pts.astype(np.float32)
        pts = self.to_cartesian(pts)
        tree = KDTree(atoms["cart_pos"])
        distances, idxs = tree.query(pts)
        values = np.ones(pts.shape[0], dtype=np.float32)
        mask = distances > 1.0  # minimum bigger than 1 angstrom
        rho = density.rho(pts[mask])
        values[mask] = rho
        values = values.reshape(shape)
        verts, faces, normals, _ = marching_cubes(
            values, isovalue, spacing=separations, gradient_direction="ascent"
        )
        mesh = trimesh.Trimesh(vertices=verts, faces=faces, normals=normals,)
        return mesh

    def hirshfeld_surfaces(self, **kwargs):
        "Alias for `self.stockholder_weight_isosurfaces`"
        return self.stockholder_weight_isosurfaces(**kwargs)

    def stockholder_weight_isosurfaces(self, kind="mol", **kwargs):
        """Calculate stockholder weight isosurfaces (i.e. Hirshfeld surfaces)
        for each symmetry unique molecule or atom in this crystal.

        Parameters
        ----------
        kind: str, optional
            dictates whether we calculate surfaces for each unique molecule
            or for each unique atom

        Keyword Args
        ------------
        isovalue: float, optional
            level set value for the isosurface (default=0.5). Must be between
            0 and 1, but values other than 0.5 probably won't make sense anyway.
        separation: float, optional
            separation between density grid used in the surface calculation
            (default 0.2) in Angstroms.
        radius: float, optional
            maximum distance for contributing neighbours for the stockholder
            weight calculation
        color: str, optional
            surface property to use for vertex coloring, one of ('d_norm_i',
            'd_i', 'd_norm_e', 'd_e', 'd_norm')
        colormap: str, optional
            matplotlib colormap to use for surface coloring (default 'viridis_r')
        midpoint: float, optional, default 0.0 if using d_norm
            use the midpoint norm (as is used in CrystalExplorer)

        Returns
        -------
        list of :obj:`trimesh.Trimesh`
            A list of meshes representing the stockholder weight isosurfaces
        """
        from .density import StockholderWeight
        from .surface import stockholder_weight_isosurface
        from matplotlib.cm import get_cmap
        import trimesh

        sep = kwargs.get("separation", kwargs.get("resolution", 0.2))
        radius = kwargs.get("radius", 12.0)
        vertex_color = kwargs.get("color", "d_norm")
        isovalue = kwargs.get("isovalue", 0.5)
        midpoint = kwargs.get("midpoint", 0.0 if vertex_color == "d_norm" else None)
        meshes = []
        colormap = get_cmap(
            kwargs.get("colormap", DEFAULT_COLORMAPS.get(vertex_color, "viridis_r"))
        )
        isos = []
        if kind == "atom":
            for n, pos, neighbour_els, neighbour_pos in self.atomic_surroundings(
                radius=radius
            ):
                s = StockholderWeight.from_arrays(
                    [n], [pos], neighbour_els, neighbour_pos
                )
                iso = stockholder_weight_isosurface(s, isovalue=isovalue, sep=sep)
                isos.append(iso)
        elif kind == "mol":
            for mol, n_e, n_p in self.molecule_surroundings(radius=radius):
                s = StockholderWeight.from_arrays(
                    mol.atomic_numbers, mol.positions, n_e, n_p
                )
                iso = stockholder_weight_isosurface(s, isovalue=isovalue, sep=sep)
                isos.append(iso)
        else:
            for arr in self.functional_group_surroundings(radius=radius, kind=kind):
                s = StockholderWeight.from_arrays(*arr)
                iso = stockholder_weight_isosurface(s, isovalue=isovalue, sep=sep)
                isos.append(iso)

        for iso in isos:
            prop = iso.vertex_prop[vertex_color]
            norm = None
            if midpoint is not None:
                from matplotlib.colors import DivergingNorm

                norm = DivergingNorm(vmin=prop.min(), vcenter=midpoint, vmax=prop.max())
                prop = norm(prop)
            color = colormap(prop)
            mesh = trimesh.Trimesh(
                vertices=iso.vertices,
                faces=iso.faces,
                normals=iso.normals,
                vertex_colors=color,
            )
            meshes.append(mesh)
        return meshes

    def functional_group_shape_descriptors(
        self, l_max=5, radius=6.0, kind="carboxylic_acid"
    ):
        """Calculate the shape descriptors[1,2] for the all atoms in the functional group
        given for all symmetry unique molecules in this crystal.

        Parameters
        ----------
        l_max: int, optional
            maximum level of angular momenta to include in the spherical harmonic
            transform of the molecular shape function. (default: 5)
        radius: float, optional
            maximum distance (Angstroms) of neighbouring atoms to include in 
            stockholder weight calculation (default: 5)
        kind: str, optional
            Identifier for the functional group type (default: 'carboxylic_acid')

        Returns
        -------
        :obj:`np.ndarray`
            shape description vector

        References
        ----------
        [1] PR Spackman et al. Sci. Rep. 6, 22204 (2016)
            https://dx.doi.org/10.1038/srep22204
        [2] PR Spackman et al. Angew. Chem. 58 (47), 16780-16784 (2019)
            https://dx.doi.org/10.1002/anie.201906602

        """
        descriptors = []
        from .sht import SHT
        from .shape_descriptors import stockholder_weight_descriptor

        sph = SHT(l_max=l_max)
        for (
            in_els,
            in_pos,
            neighbour_els,
            neighbour_pos,
        ) in self.functional_group_surroundings(radius=radius):
            masses = np.asarray([Element[x].mass for x in in_els])
            c = np.sum(in_pos * masses[:, np.newaxis] / np.sum(masses), axis=0).astype(
                np.float32
            )
            dists = np.linalg.norm(in_pos - c, axis=1)
            bounds = np.min(dists) / 2, np.max(dists) + 10.0
            descriptors.append(
                stockholder_weight_descriptor(
                    sph,
                    in_els,
                    in_pos,
                    neighbour_els,
                    neighbour_pos,
                    origin=c,
                    bounds=bounds,
                )
            )
        return np.asarray(descriptors)

    def molecular_shape_descriptors(self, l_max=5, radius=6.0):
        """Calculate the molecular shape descriptors[1,2] for all symmetry unique
        molecules in this crystal.

        Parameters
        ----------
        l_max: int, optional
            maximum level of angular momenta to include in the spherical harmonic
            transform of the molecular shape function.

        Returns
        -------
        :obj:`np.ndarray`
            shape description vector

        References
        ----------
        [1] PR Spackman et al. Sci. Rep. 6, 22204 (2016)
            https://dx.doi.org/10.1038/srep22204
        [2] PR Spackman et al. Angew. Chem. 58 (47), 16780-16784 (2019)
            https://dx.doi.org/10.1002/anie.201906602

        """
        descriptors = []
        from .sht import SHT
        from .shape_descriptors import stockholder_weight_descriptor

        sph = SHT(l_max=l_max)
        for mol, neighbour_els, neighbour_pos in self.molecule_surroundings(
            radius=radius
        ):
            c = np.array(mol.centroid, dtype=np.float32)
            dists = np.linalg.norm(mol.positions - c, axis=1)
            bounds = np.min(dists) / 2, np.max(dists) + 10.0
            descriptors.append(
                stockholder_weight_descriptor(
                    sph,
                    mol.atomic_numbers,
                    mol.positions,
                    neighbour_els,
                    neighbour_pos,
                    origin=c,
                    bounds=bounds,
                )
            )
        return np.asarray(descriptors)

    def atomic_shape_descriptors(self, l_max=5, radius=3.8):
        """Calculate the shape descriptors[1,2] for all symmetry unique
        atoms in this crystal.

        Parameters
        ----------
        l_max: int, optional
            maximum level of angular momenta to include in the spherical harmonic
            transform of the shape function.

        Returns
        -------
        :obj:`np.ndarray`
            shape description vector

        References
        ----------
        [1] PR Spackman et al. Sci. Rep. 6, 22204 (2016)
            https://dx.doi.org/10.1038/srep22204
        [2] PR Spackman et al. Angew. Chem. 58 (47), 16780-16784 (2019)
            https://dx.doi.org/10.1002/anie.201906602
        """
        descriptors = []
        from .sht import SHT
        from .shape_descriptors import stockholder_weight_descriptor

        sph = SHT(l_max=l_max)
        for n, pos, neighbour_els, neighbour_pos in self.atomic_surroundings(
            radius=radius
        ):
            ubound = Element[n].vdw_radius * 3
            descriptors.append(
                stockholder_weight_descriptor(
                    sph, [n], [pos], neighbour_els, neighbour_pos, bounds=(0.2, ubound)
                )
            )
        return np.asarray(descriptors)

    def atom_group_shape_descriptors(self, atoms, l_max=5, radius=3.8):
        """Calculate the shape descriptors[1,2] for the given atomic
        group in this crystal.

        Parameters
        ----------
        l_max: int, optional
            maximum level of angular momenta to include in the spherical harmonic
            transform of the shape function.

        Returns
        -------
        :obj:`np.ndarray`
            shape description vector

        References
        ----------
        [1] PR Spackman et al. Sci. Rep. 6, 22204 (2016)
            https://dx.doi.org/10.1038/srep22204
        [2] PR Spackman et al. Angew. Chem. 58 (47), 16780-16784 (2019)
            https://dx.doi.org/10.1002/anie.201906602
        """
        from .sht import SHT
        from .shape_descriptors import stockholder_weight_descriptor

        sph = SHT(l_max=l_max)
        inside, outside = self.atom_group_surroundings(atoms, radius=radius)
        m = Molecule.from_arrays(*inside)
        c = np.array(m.centroid, dtype=np.float32)
        dists = np.linalg.norm(m.positions - c, axis=1)
        bounds = np.min(dists) / 2, np.max(dists) + 10.0
        return np.asarray(
            stockholder_weight_descriptor(
                sph, *inside, *outside, origin=c, bounds=bounds
            )
        )

    @property
    def site_labels(self):
        "array of labels for sites in the asymmetric_unit"
        return self.asymmetric_unit.labels

    def __repr__(self):
        if "lattice_energy" in self.properties and "density" in self.properties:
            return "<Crystal {} {} ({:.3f}, {:.3f})>".format(
                self.asymmetric_unit.formula,
                self.space_group.symbol,
                self.properties["density"],
                self.properties["lattice_energy"],
            )
        return "<Crystal {} {}>".format(
            self.asymmetric_unit.formula, self.space_group.symbol
        )

    @property
    def density(self):
        "Calculated density of this crystal structure in g/cm^3"
        if "density" in self.properties:
            return self.properties["density"]
        uc_mass = sum(Element[x].mass for x in self.unit_cell_atoms()["element"])
        uc_vol = self.unit_cell.volume()
        return uc_mass / uc_vol / 0.6022

    @classmethod
    def load(cls, filename):
        """Load a crystal structure from file (.res, .cif)

        Parameters
        ----------
        filename: str
            the path to the crystal structure file

        Returns
        -------
        :obj:`Crystal`
            the resulting crystal structure
        """
        extension_map = {".cif": cls.from_cif_file, ".res": cls.from_shelx_file}
        extension = os.path.splitext(filename)[-1].lower()
        return extension_map[extension](filename)

    @classmethod
    def from_cif_data(cls, cif_data, titl=None):
        """Initialize a crystal structure from a dictionary
        of CIF data"""
        labels = cif_data.get("atom_site_label", None)
        symbols = cif_data.get("atom_site_type_symbol", None)
        elements = [Element[x] for x in symbols]
        x = np.asarray(cif_data.get("atom_site_fract_x", []))
        y = np.asarray(cif_data.get("atom_site_fract_y", []))
        z = np.asarray(cif_data.get("atom_site_fract_z", []))
        occupation = np.asarray(cif_data.get("atom_site_occupancy", [1] * len(x)))
        frac_pos = np.array([x, y, z]).T
        asym = AsymmetricUnit(
            elements=elements, positions=frac_pos, labels=labels, occupation=occupation
        )
        lengths = [cif_data[f"cell_length_{x}"] for x in ("a", "b", "c")]
        angles = [cif_data[f"cell_angle_{x}"] for x in ("alpha", "beta", "gamma")]
        unit_cell = UnitCell.from_lengths_and_angles(lengths, angles, unit="degrees")
        space_group = SpaceGroup(int(cif_data.get("symmetry_Int_Tables_number", 1)))

        if "symmetry_equiv_pos_as_xyz" in cif_data:
            latt = space_group.latt
            symops = [
                SymmetryOperation.from_string_code(x)
                for x in cif_data["symmetry_equiv_pos_as_xyz"]
            ]
            try:
                new_sg = SpaceGroup.from_symmetry_operations(symops)
                space_group = new_sg
            except ValueError as e:
                space_group.symmetry_operations = symops
                symbol = cif_data.get(
                    "symmetry_space_group_name_H-M", space_group.symbol
                )
                space_group.symbol = symbol
                space_group.full_symbol = symbol
                LOG.warn(
                    "Initializing non-standard spacegroup setting %s, "
                    "some SG data may be missing",
                    symbol,
                )

        elif "symmetry_Int_Tables_number" in cif_data:
            space_group = SpaceGroup(cif_data["symmetry_Int_Tables_number"])

        return Crystal(unit_cell, space_group, asym, cif_data=cif_data, titl=titl)

    @classmethod
    def from_cif_file(cls, filename, data_block_name=None):
        """Initialize a crystal structure from a CIF file"""
        cif = Cif.from_file(filename)
        if data_block_name is not None:
            return cls.from_cif_data(cif.data[data_block_name], titl=data_block_name)

        crystals = {
            name: cls.from_cif_data(data, titl=name) for name, data in cif.data.items()
        }
        keys = list(crystals.keys())
        if len(keys) == 1:
            return crystals[keys[0]]
        return crystals

    @classmethod
    def from_cif_string(cls, file_content, **kwargs):
        data_block_name = kwargs.get("data_block_name", None)
        cif = Cif.from_string(file_content)
        if data_block_name is not None:
            return cls.from_cif_data(cif.data[data_block_name], titl=data_block_name)

        crystals = {
            name: cls.from_cif_data(data, titl=name) for name, data in cif.data.items()
        }
        keys = list(crystals.keys())
        if len(keys) == 1:
            return crystals[keys[0]]
        return crystals

    @classmethod
    def from_shelx_file(cls, filename, **kwargs):
        """Initialize a crystal structure from a shelx .res file"""
        p = Path(filename)
        titl = p.stem
        return cls.from_shelx_string(p.read_text(), titl=titl, **kwargs)

    @classmethod
    def from_shelx_string(cls, file_content, **kwargs):
        """Initialize a crystal structure from a shelx .res string"""
        from .shelx import parse_shelx_file_content

        shelx_dict = parse_shelx_file_content(file_content)
        asymmetric_unit = AsymmetricUnit.from_records(shelx_dict["ATOM"])
        space_group = SpaceGroup.from_symmetry_operations(
            shelx_dict["SYMM"], expand_latt=shelx_dict["LATT"]
        )
        unit_cell = UnitCell.from_lengths_and_angles(
            shelx_dict["CELL"]["lengths"], shelx_dict["CELL"]["angles"], unit="degrees"
        )
        return cls(unit_cell, space_group, asymmetric_unit, **kwargs)

    @property
    def name(self):
        "synonym for titl"
        return self.titl

    @property
    def id(self):
        "synonym for titl"
        return self.titl

    @property
    def titl(self):
        if "titl" in self.properties:
            return self.properties["titl"]
        return self.asymmetric_unit.formula

    def to_cif_data(self, data_block_name=None):
        if data_block_name is None:
            data_block_name = self.titl
        if "cif_data" in self.properties:
            cif_data = self.properties["cif_data"]
        else:
            cif_data = {
                "audit_creation_method": "generated by cspy2.0",
                "symmetry_equiv_pos_site_id": list(
                    range(1, len(self.symmetry_operations) + 1)
                ),
                "symmetry_equiv_pos_as_xyz": [str(x) for x in self.symmetry_operations],
                "cell_length_a": self.unit_cell.a,
                "cell_length_b": self.unit_cell.b,
                "cell_length_c": self.unit_cell.c,
                "cell_angle_alpha": self.unit_cell.alpha_deg,
                "cell_angle_beta": self.unit_cell.beta_deg,
                "cell_angle_gamma": self.unit_cell.gamma_deg,
                "atom_site_label": self.asymmetric_unit.labels,
                "atom_site_type_symbol": [
                    x.symbol for x in self.asymmetric_unit.elements
                ],
                "atom_site_fract_x": self.asymmetric_unit.positions[:, 0],
                "atom_site_fract_y": self.asymmetric_unit.positions[:, 1],
                "atom_site_fract_z": self.asymmetric_unit.positions[:, 2],
                "atom_site_occupancy": self.asymmetric_unit.properties.get(
                    "occupation", np.ones(len(self.asymmetric_unit))
                ),
            }
        return {data_block_name: cif_data}

    def to_cif_file(self, filename, **kwargs):
        cif_data = self.to_cif_data(**kwargs)
        return Cif(cif_data).to_file(filename)

    def to_cif_string(self, **kwargs):
        cif_data = self.to_cif_data(**kwargs)
        return Cif(cif_data).to_string()

    def to_shelx_file(self, filename):
        """Write this crystal structure as a shelx .res file"""
        Path(filename).write_text(self.to_shelx_string())

    def to_shelx_string(self, titl=None):
        """Represent this crystal structure as a shelx .res string"""
        from shmolecule.shelx import to_res_contents

        sfac = list(np.unique(self.site_atoms))
        atom_sfac = [sfac.index(x) + 1 for x in self.site_atoms]
        shelx_data = {
            "TITL": self.titl if titl is None else titl,
            "CELL": self.unit_cell.parameters,
            "SFAC": [Element[x].symbol for x in sfac],
            "SYMM": [
                str(s)
                for s in self.space_group.reduced_symmetry_operations()
                if not s.is_identity()
            ],
            "LATT": self.space_group.latt,
            "ATOM": [
                "{:3} {:3} {: 20.12f} {: 20.12f} {: 20.12f}".format(l, s, *pos)
                for l, s, pos in zip(
                    self.asymmetric_unit.labels, atom_sfac, self.site_positions
                )
            ],
        }
        return to_res_contents(shelx_data)

    def save(self, filename):
        """Save this crystal structure to file (.cif)"""
        extension_map = {".cif": self.to_cif_file, ".res": self.to_shelx_file}
        extension = os.path.splitext(filename)[-1].lower()
        return extension_map[extension](filename)
