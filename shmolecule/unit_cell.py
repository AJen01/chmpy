import logging
import numpy as np
from numpy import zeros, allclose as close

LOG = logging.getLogger(__name__)


class UnitCell:
    """Storage class for the lattice vectors of a crystal
    i.e. its unit cell.

    Create a UnitCell object from a list of lattice vectors or
    a row major direct matrix. Unless otherwise specified, length
    units are Angstroms, and angular units are radians.

    Parameters
    ----------
    vectors: array_like
        (3, 3) array of lattice vectors, row major i.e. vectors[0, :] is
        lattice vector A etc.
    """

    def __init__(self, vectors):
        self.set_vectors(vectors)

    @property
    def lattice(self):
        "The direct matrix of this unit cell i.e. vectors of the lattice"
        return self.direct

    @property
    def reciprocal_lattice(self):
        "The reciprocal matrix of this unit cell i.e. vectors of the reciprocal lattice"
        return self.inverse.T

    def to_cartesian(self, coords):
        """Transform coordinates from fractional space (a, b, c)
        to Cartesian space (x, y, z). The x-direction will be aligned
        along lattice vector A.

        Parameters
        ----------
        coords : array_like
            (N, 3) array of fractional coordinates

        Returns
        -------
        :obj:`np.ndarray`
            (N, 3) array of Cartesian coordinates
        """
        return np.dot(coords, self.direct)

    def to_fractional(self, coords):
        """Transform coordinates from Cartesian space (x, y, z)
        to fractional space (a, b, c). The x-direction will is assumed
        be aligned along lattice vector A.

        Parameters
        ----------
        coords : array_like
            (N, 3) array of Cartesian coordinates

        Returns
        -------
        :obj:`np.ndarray`
            (N, 3) array of fractional coordinates
        """
        return np.dot(coords, self.inverse)

    def set_lengths_and_angles(self, lengths, angles):
        """Modify this unit cell by setting the lattice vectors
        according to lengths a, b, c and angles alpha, beta, gamma of
        a parallelipiped.

        Parameters
        ----------
        lengths : array_like
            array of (a, b, c), the unit cell side lengths in Angstroms.

        angles : array_like
            array of (alpha, beta, gamma), the unit cell angles lengths
            in radians.
        """
        self.lengths = lengths
        self.angles = angles
        a, b, c = self.lengths
        ca, cb, cg = np.cos(self.angles)
        sg = np.sin(self.angles[2])
        v = self.volume()
        self.direct = np.transpose(
            [
                [a, b * cg, c * cb],
                [0, b * sg, c * (ca - cb * cg) / sg],
                [0, 0, v / (a * b * sg)],
            ]
        )
        r = [
            [1 / a, 0.0, 0.0],
            [-cg / (a * sg), 1 / (b * sg), 0],
            [
                b * c * (ca * cg - cb) / v / sg,
                a * c * (cb * cg - ca) / v / sg,
                a * b * sg / v,
            ],
        ]
        self.inverse = np.array(r)
        self.set_cell_type()

    def set_vectors(self, vectors):
        """Modify this unit cell by setting the lattice vectors
        according to those provided. This is performed by setting the
        lattice parameters (lengths and angles) based on the provided vectors,
        such that it results in a consistent basis without directly
        matrix inverse (and typically losing precision), and
        as the SHELX file/CIF output will be relying on these
        lengths/angles anyway, it is important to have these consistent.


        Parameters
        ----------
        vectors : array_like
            (3, 3) array of lattice vectors, row major i.e. vectors[0, :] is
            lattice vector A etc.
        """
        self.direct = vectors
        params = zeros(6)
        a, b, c = np.linalg.norm(self.direct, axis=1)
        u_a = vectors[0, :] / a
        u_b = vectors[1, :] / b
        u_c = vectors[2, :] / c
        alpha = np.arccos(np.clip(np.vdot(u_b, u_c), -1, 1))
        beta = np.arccos(np.clip(np.vdot(u_c, u_a), -1, 1))
        gamma = np.arccos(np.clip(np.vdot(u_a, u_b), -1, 1))
        params[3:] = np.degrees([alpha, beta, gamma])
        self.lengths = [a, b, c]
        self.angles = [alpha, beta, gamma]
        self.inverse = np.linalg.inv(self.direct)
        self.set_cell_type()

    def set_cell_type(self):
        if self.is_cubic:
            self.cell_type_index = 6
            self.cell_type = "cubic"
            self.unique_parameters = (self.a,)
        elif self.is_rhombohedral:
            self.cell_type_index = 4
            self.cell_type = "rhombohedral"
            self.unique_parameters = self.a, self.alpha
        elif self.is_hexagonal:
            self.cell_type_index = 5
            self.cell_type = "hexagonal"
            self.unique_parameters = self.a, self.c
        elif self.is_tetragonal:
            self.cell_type_index = 3
            self.cell_type = "tetragonal"
            self.unique_parameters = self.a, self.c
        elif self.is_orthorhombic:
            self.cell_type_index = 2
            self.cell_type = "orthorhombic"
            self.unique_parameters = self.a, self.b, self.c
        elif self.is_monoclinic:
            self.cell_type_index = 1
            self.cell_type = "monoclinic"
            self.unique_parameters = self.a, self.b, self.c, self.beta
        else:
            self.cell_type_index = 0
            self.cell_type = "triclinic"
            self.unique_parameters = (
                self.a,
                self.b,
                self.c,
                self.alpha,
                self.beta,
                self.gamma,
            )

    def volume(self):
        """The volume of the unit cell, in cubic Angstroms"""
        a, b, c = self.lengths
        ca, cb, cg = np.cos(self.angles)
        return a * b * c * np.sqrt(1 - ca * ca - cb * cb - cg * cg + 2 * ca * cb * cg)

    @property
    def abc_equal(self):
        return close(np.array(self.lengths) - self.lengths[0], zeros(3))

    @property
    def abc_different(self):
        return not (
            close(self.a, self.b) or close(self.a, self.c) or close(self.b, self.c)
        )

    @property
    def orthogonal(self):
        return close(np.abs(self.angles) - np.pi / 2, zeros(3))

    @property
    def angles_different(self):
        return not (
            close(self.alpha, self.beta)
            or close(self.alpha, self.gamma)
            or close(self.beta, self.gamma)
        )

    @property
    def is_triclinic(self):
        """Returns true if angles and lengths are different"""
        return self.abc_different and self.angles_different

    @property
    def is_monoclinic(self):
        """Returns true if angles alpha and gamma are equal"""
        return close(self.alpha, self.gamma) and self.abc_different

    @property
    def is_cubic(self):
        """Returns true if all lengths are equal and all angles are 90 degrees"""
        return self.abc_equal and self.orthogonal

    @property
    def is_orthorhombic(self):
        """Returns true if all angles are 90 degrees"""
        return self.orthogonal and self.abc_different

    @property
    def is_tetragonal(self):
        """Returns true if a, b are equal and all angles are 90 degrees"""
        return close(self.a, self.b) and (not close(self.a, self.c)) and self.orthogonal

    @property
    def is_rhombohedral(self):
        """Returns true if all lengths are equal and all angles are equal"""
        return (
            self.abc_equal
            and close(np.array(self.angles) - self.angles[0], zeros(3))
            and (not close(self.alpha, np.pi / 2))
        )

    @property
    def is_hexagonal(self):
        """Returns true if all lengths are equal and all angles are equal"""
        return (
            close(self.a, self.b)
            and (not close(self.a, self.c))
            and close(self.angles[:2], np.pi / 2)
            and close(self.gamma, 2 * np.pi / 3)
        )

    @property
    def a(self):
        "Length of lattice vector a"
        return self.lengths[0]

    @property
    def alpha(self):
        "Angle between lattice vectors b and c"
        return self.angles[0]

    @property
    def b(self):
        "Length of lattice vector b"
        return self.lengths[1]

    @property
    def beta(self):
        "Angle between lattice vectors a and c"
        return self.angles[1]

    @property
    def c(self):
        "Length of lattice vector c"
        return self.lengths[2]

    @property
    def gamma(self):
        "Angle between lattice vectors a and b"
        return self.angles[2]

    @property
    def alpha_deg(self):
        "Angle between lattice vectors b and c in degrees"
        return np.degrees(self.angles[0])

    @property
    def beta_deg(self):
        "Angle between lattice vectors a and c in degrees"
        return np.degrees(self.angles[1])

    @property
    def gamma_deg(self):
        "Angle between lattice vectors a and b in degrees"
        return np.degrees(self.angles[2])

    @property
    def parameters(self):
        "single vector of lattice side lengths and angles in degrees"
        atol = 1e-6
        l = np.array(self.lengths)
        deg = np.degrees(self.angles)
        len_diffs = np.abs(l[:, np.newaxis] - l[np.newaxis, :]) < atol
        ang_diffs = np.abs(deg[:, np.newaxis] - deg[np.newaxis, :]) < atol
        for i in range(3):
            l[len_diffs[i]] = l[i]
            deg[ang_diffs[i]] = deg[i]
        return np.hstack((l, deg))

    @classmethod
    def from_lengths_and_angles(cls, lengths, angles, unit="radians"):
        """Construct a new UnitCell from the provided lengths and angles.

        Parameters
        ----------
        lengths : array_like
            Lattice side lengths (a, b, c) in Angstroms.

        angles : array_like
            Lattice angles (alpha, beta, gamma) in provided units (default radians)

        unit : str, optional
            Unit for angles i.e. 'radians' or 'degrees' (default radians).

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """
        uc = cls(np.eye(3))
        if unit == "radians":
            if np.any(np.abs(angles) > np.pi):
                LOG.warn(
                    "Large angle in UnitCell.from_lengths_and_angles, "
                    "are you sure your angles are not in degrees?"
                )
            uc.set_lengths_and_angles(lengths, angles)
        else:
            uc.set_lengths_and_angles(lengths, np.radians(angles))
        return uc

    @classmethod
    def cubic(cls, length):
        """Construct a new cubic UnitCell from the provided side length.

        Parameters
        ----------
        length : float
            Lattice side length a in Angstroms.

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """
        return cls(np.eye(3) * length)

    @classmethod
    def from_unique_parameters(cls, params, cell_type="triclinic", **kwargs):
        return getattr(cls, cell_type)(*params)

    @classmethod
    def triclinic(cls, *params, **kwargs):
        """Construct a new UnitCell from the provided side lengths and angles.

        Parameters
        ----------
        params: array_like
            Lattice side lengths and angles (a, b, c, alpha, beta, gamma)

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """

        assert len(params) == 6, "Requre three lengths and angles for Triclinic cell"
        return cls.from_lengths_and_angles(params[:3], params[3:], **kwargs)

    @classmethod
    def monoclinic(cls, *params, **kwargs):
        """Construct a new UnitCell from the provided side lengths and angle.

        Parameters
        ----------
        params: array_like
            Lattice side lengths and angles (a, b, c, beta)

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """

        assert (
            len(params) == 4
        ), "Requre three lengths and one angle for Monoclinic cell"
        unit = kwargs.get("unit", "radians")
        if unit != "radians":
            alpha, gamma = 90, 90
        else:
            alpha, gamma = np.pi / 2, np.pi / 2
        return cls.from_lengths_and_angles(
            params[:3], (alpha, params[3], gamma), **kwargs
        )

    @classmethod
    def tetragonal(cls, *params, **kwargs):
        """Construct a new UnitCell from the provided side lengths and angles.

        Parameters
        ----------
        params: array_like
            Lattice side lengths (a, c)

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """
        assert len(params) == 2, "Requre 2 lengths for Tetragonal cell"
        unit = kwargs.get("unit", "radians")
        if unit != "radians":
            angles = [90] * 3
        else:
            angles = [np.pi / 2] * 3
        return cls.from_lengths_and_angles(
            (params[0], params[0], params[1]), angles, **kwargs
        )

    @classmethod
    def hexagonal(cls, *params, **kwargs):
        """Construct a new UnitCell from the provided side lengths and angles.

        Parameters
        ----------
        params: array_like
            Lattice side lengths (a, c)

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """
        assert len(params) == 2, "Requre 2 lengths for Hexagonal cell"
        unit = kwargs.pop("unit", "radians")
        unit = "radians"
        angles = [np.pi / 2, np.pi / 2, 2 * np.pi / 3]
        return cls.from_lengths_and_angles(
            (params[0], params[0], params[1]), angles, unit=unit, **kwargs
        )

    @classmethod
    def rhombohedral(cls, *params, **kwargs):
        """Construct a new UnitCell from the provided side lengths and angles.

        Parameters
        ----------
        params: array_like
            Lattice side length a and angle alpha c

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """
        assert len(params) == 2, "Requre 1 length and 1 angle for Rhombohedral cell"
        return cls.from_lengths_and_angles([params[0]] * 3, [params[1]] * 3, **kwargs)

    @classmethod
    def orthorhombic(cls, *lengths, **kwargs):
        """Construct a new orthorhombic UnitCell from the provided side lengths.

        Parameters
        ----------
        lengths : array_like
            Lattice side lengths (a, b, c) in Angstroms.

        Returns
        -------
        UnitCell
            A new unit cell object representing the provided lattice.
        """

        assert len(lengths) == 3, "Requre three lengths for Orthorhombic cell"
        return cls(np.diag(lengths))

    def __repr__(self):
        cell = self.cell_type
        unique = self.unique_parameters
        s = "<{{}}: {{}} ({})>".format(",".join("{:.3f}" for p in unique))
        return s.format(self.__class__.__name__, cell, *unique)