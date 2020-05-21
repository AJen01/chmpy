from chmpy import StockholderWeight, PromoleculeDensity
from chmpy.util.num import spherical_to_cartesian
from scipy.optimize import minimize_scalar
from chmpy.interpolate._density import sphere_stockholder_radii, sphere_promolecule_radii
from ._invariants import p_invariants_c, p_invariants_r
import logging
import numpy as np

LOG = logging.getLogger(__name__)
_HAVE_WARNED_ABOUT_LMAX_P = False


def make_N_invariants(coefficients, kind="real"):
    """Construct the 'N' type invariants from sht coefficients.
    If coefficients is of length n, the size of the result will be sqrt(n)

    Arguments:
    coefficients -- the set of spherical harmonic coefficients
    """
    if kind == "complex":
        size = int(np.sqrt(len(coefficients)))
        invariants = np.empty(shape=(size), dtype=np.float64)
        for i in range(0, size):
            lower, upper = i ** 2, (i + 1) ** 2
            invariants[i] = np.sum(
                coefficients[lower : upper + 1]
                * np.conj(coefficients[lower : upper + 1])
            ).real
        return np.sqrt(invariants)
    else:
        # n = (l_max +2)(l_max+1)/2
        n = len(coefficients)
        size = int((-3 + np.sqrt(8 * n + 1)) // 2) + 1
        lower = 0
        invariants = np.empty(shape=(size), dtype=np.float64)
        for i in range(0, size):
            x = i + 1
            upper = lower + x
            invariants[i] = np.sum(
                coefficients[lower : upper + 1]
                * np.conj(coefficients[lower : upper + 1])
            ).real
            lower += x
        return np.sqrt(invariants)


def make_invariants(l_max, coefficients, kinds="NP"):
    global _HAVE_WARNED_ABOUT_LMAX_P
    invariants = []
    if "N" in kinds:
        invariants.append(make_N_invariants(coefficients))
    if "P" in kinds:
        # Because we only have factorial precision in our
        # clebsch implementation up to 70! l_max for P type
        # invariants is restricted to < 23
        if l_max > 23:
            if not _HAVE_WARNED_ABOUT_LMAX_P:
                LOG.warn(
                    "P type invariants only supported up to l_max = 23: "
                    "will only using N type invariants beyond that."
                )
                _HAVE_WARNED_ABOUT_LMAX_P = True
            c = coefficients[: (25 * 24) // 2]
            invariants.append(p_invariants_r(c))
        else:
            invariants.append(p_invariants_r(coefficients))
    return np.hstack(invariants)


def stockholder_weight_descriptor(sht, n_i, p_i, n_e, p_e, **kwargs):
    isovalue = kwargs.get("isovalue", 0.5)
    background = kwargs.get("background", 0.0)
    property_function = kwargs.get("with_property", None)
    r_min, r_max = kwargs.get("bounds", (0.1, 20.0))
    s = StockholderWeight.from_arrays(n_i, p_i, n_e, p_e, background=background)
    g = np.empty(sht.grid.shape, dtype=np.float32)
    g[:, :] = sht.grid[:, :]
    o = kwargs.get("origin", np.mean(p_i, axis=0, dtype=np.float32))
    r = sphere_stockholder_radii(s.s, o, g, r_min, r_max, 1e-7, 30, isovalue)
    if property_function is not None:
        if property_function == "d_norm":
            property_function = s.d_norm
        elif property_function == "esp":
            from chmpy import Molecule

            els = s.dens_a.elements
            pos = s.dens_a.positions
            property_function = Molecule.from_arrays(
                s.dens_a.elements, s.dens_a.positions
            ).electrostatic_potential
        xyz = sht.grid_cartesian * r[:, np.newaxis]
        prop_values = property_function(xyz)
        r_cplx = np.empty(r.shape, dtype=np.complex128)
        r_cplx.real = r
        r_cplx.imag = prop_values
        r = r_cplx
    l_max = sht.l_max
    coeffs = sht.analyse(r)
    invariants = make_invariants(l_max, coeffs)
    if kwargs.get("coefficients", False):
        return coeffs, invariants
    return invariants


def promolecule_density_descriptor(sht, n_i, p_i, **kwargs):
    isovalue = kwargs.get("isovalue", 0.0002)
    property_function = kwargs.get("with_property", None)
    r_min, r_max = kwargs.get("bounds", (0.4, 20.0))
    pro = PromoleculeDensity((n_i, p_i))
    g = np.empty(sht.grid.shape, dtype=np.float32)
    g[:, :] = sht.grid[:, :]
    o = kwargs.get("origin", np.mean(p_i, axis=0, dtype=np.float32))
    r = sphere_promolecule_radii(pro.dens, o, g, r_min, r_max, 1e-7, 30, isovalue)
    if property_function is not None:
        if property_function == "d_norm":
            property_function = lambda x: pro.d_norm(x)[1]
        elif property_function == "esp":
            from chmpy import Molecule

            els = pro.elements
            pos = pro.positions
            property_function = Molecule.from_arrays(els, pos).electrostatic_potential
        xyz = sht.grid_cartesian * r[:, np.newaxis]
        prop_values = property_function(xyz)
        r_cplx = np.empty(r.shape, dtype=np.complex128)
        r_cplx.real = r
        r_cplx.imag = prop_values
        r = r_cplx
    l_max = sht.l_max
    coeffs = sht.analyse(r)
    invariants = make_invariants(l_max, coeffs)
    if kwargs.get("coefficients", False):
        return coeffs, invariants
    return invariants