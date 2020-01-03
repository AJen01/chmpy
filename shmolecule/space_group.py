import numpy as np
from collections import namedtuple
import os
import json
import re
from fractions import Fraction
from .util import cartesian_product
import logging

LOG = logging.getLogger(__name__)

_sgdata = namedtuple(
    "_sgdata",
    "number short schoenflies "
    "full international pointgroup "
    "choice centering symops centrosymmetric",
)

with open(os.path.join(os.path.dirname(__file__), "sgdata.json")) as f:
    _sgdata_dict = json.load(f)

SG_FROM_NUMBER = {k: [_sgdata._make(x) for x in v] for k, v in _sgdata_dict.items()}

SG_FROM_SYMOPS = {tuple(x.symops): x for k, sgs in SG_FROM_NUMBER.items() for x in sgs}
SG_CHOICES = {int(k): [x.choice for x in v] for k, v in SG_FROM_NUMBER.items()}

SYMM_STR_SYMBOL_REGEX = re.compile(r".*?([+-]*[xyz0-9\/\.]+)")

LATTICE_TYPE_TRANSLATIONS = {
    1: (),
    2: ((1 / 2, 1 / 2, 1 / 2),),  # P
    3: ((2 / 3, 1 / 3, 1 / 3), (1 / 3, 2 / 3, 2 / 3)),  # R
    4: ((0, 1 / 2, 1 / 2), (1 / 2, 0, 1 / 2), (1 / 2, 1 / 2, 0)),  # F
    5: ((0, 1 / 2, 1 / 2),),  # A
    6: ((1 / 2, 0, 1 / 2),),  # B
    7: ((1 / 2, 1 / 2, 0),),  # C
}


def encode_symm_str(rotation, translation):
    symbols = "xyz"
    res = []
    for i in (0, 1, 2):
        t = Fraction(translation[i]).limit_denominator(12)
        v = ""
        if t != 0:
            v += str(t)
        for j in range(0, 3):
            c = rotation[i][j]
            if c != 0:
                s = "-" if c < 0 else "+"
                v += s + symbols[j]
        res.append(v)
    res = ",".join(res)
    return res


def decode_symm_str(s):
    """Decode a symmetry operation represented in the string
    form e.g. '1/2 + x, y, -z -0.25' """
    rotation = np.zeros((3, 3), dtype=np.float64)
    translation = np.zeros((3,), dtype=np.float64)
    tokens = s.lower().replace(" ", "").split(",")
    for i, row in enumerate(tokens):
        fac = 1
        row = row.strip()
        symbols = re.findall(SYMM_STR_SYMBOL_REGEX, row)
        for symbol in symbols:
            if "x" in symbol:
                idx = 0
                fac = -1 if "-x" in symbol else 1
                rotation[i, idx] = fac
            elif "y" in symbol:
                idx = 1
                fac = -1 if "-y" in symbol else 1
                rotation[i, idx] = fac
            elif "z" in symbol:
                idx = 2
                fac = -1 if "-z" in symbol else 1
                rotation[i, idx] = fac
            else:
                if "/" in symbol:
                    numerator, denominator = symbol.split("/")
                    translation[i] = Fraction(
                        Fraction(numerator), Fraction(denominator)
                    )
                else:
                    translation[i] += float(Fraction(symbol))
    translation = translation % 1
    return rotation, translation


def decode_symm_int(coded_integer):
    """A space group operation is compressed using ternary numerical system for
    rotation and duodecimal system for translation. This is achieved because
    each element of rotation matrix can have only one of {-1,0,1}, and
    the translation can have one of {0,2,3,4,6,8,9,10} divided by 12.
    Therefore 3^9 * 12^3 = 34012224 different values can map space
    group operations. In principle, octal numerical system can be used
    for translation, but duodecimal system is more convenient."""
    r = coded_integer % 19683  # 19683 = 3**9
    shift = 6561  # 6561 = 3**8
    rotation = np.empty((3, 3), dtype=np.float64)
    translation = np.empty(3, dtype=np.float64)
    for i in (0, 1, 2):
        for j in (0, 1, 2):
            # we need integer division here
            rotation[i, j] = (r % (shift * 3)) // shift - 1
            shift //= 3

    t = coded_integer // 19683
    shift = 144
    for i in (0, 1, 2):
        # we need integer division here by shift
        translation[i] = ((t % (shift * 12)) // shift) / 12
        shift //= 12
    return rotation, translation


def encode_symm_int(rotation, translation):
    r = 0
    shift = 1
    # encode rotation component
    rotation = np.round(rotation).astype(int) + 1
    for i in (2, 1, 0):
        for j in (2, 1, 0):
            r += rotation[i, j] * shift
            shift *= 3
    t = 0
    shift = 1
    translation = np.round(translation * 12).astype(int)
    for i in (2, 1, 0):
        t += translation[i] * shift
        shift *= 12
    return r + t * 19683


class SymmetryOperation:
    def __init__(self, rotation, translation):
        self.rotation = rotation
        self.translation = translation % 1

    @property
    def seitz_matrix(self):
        "The Seitz matrix form of this SymmetryOperation"
        s = np.eye(4, dtype=np.float64)
        s[:3, :3] = self.rotation
        s[:3, 3] = self.translation
        return s

    @property
    def integer_code(self):
        "Represent this SymmetryOperation as a packed integer"
        if not hasattr(self, "_integer_code"):
            setattr(
                self, "_integer_code", encode_symm_int(self.rotation, self.translation)
            )
        return getattr(self, "_integer_code")

    @property
    def cif_form(self):
        return str(self)

    def inverted(self):
        return SymmetryOperation(-self.rotation, -self.translation)

    def __add__(self, value):
        return SymmetryOperation(self.rotation, self.translation + value)

    def __sub__(self, value):
        return SymmetryOperation(self.rotation, self.translation - value)

    def apply(self, coordinates):
        if coordinates.shape[1] == 4:
            return np.dot(coordinates, self.seitz.T)
        else:
            return np.dot(coordinates, self.rotation.T) + self.translation

    def __str__(self):
        if not hasattr(self, "_string_code"):
            setattr(
                self, "_string_code", encode_symm_str(self.rotation, self.translation)
            )
        return getattr(self, "_string_code")

    def __lt__(self, other):
        return self.integer_code < other.integer_code

    def __eq__(self, other):
        return self.integer_code == other.integer_code

    def __hash__(self):
        return int(self.integer_code)

    def __repr__(self):
        return "<{}: {}>".format(self.__class__.__name__, self)

    def __call__(self, coordinates):
        return self.apply(coordinates)

    @classmethod
    def from_integer_code(cls, code):
        rot, trans = decode_symm_int(code)
        s = SymmetryOperation(rot, trans)
        setattr(s, "_integer_code", code)
        return s

    @classmethod
    def from_string_code(cls, code):
        rot, trans = decode_symm_str(code)
        s = SymmetryOperation(rot, trans)
        setattr(s, "_string_code", code)
        return s

    def is_identity(self):
        return self.integer_code == 16484

    @classmethod
    def identity(cls):
        "the identity symop i.e. x,y,z"
        return cls.from_integer_code(16484)


def expanded_symmetry_list(reduced_symops, lattice_type):
    "1=P, 2=I, 3=rhombohedral obverse on hexagonal axes, 4=F, 5=A, 6=B, 7=C."
    lattice_type_value = abs(lattice_type)
    translations = LATTICE_TYPE_TRANSLATIONS[lattice_type_value]

    identity = SymmetryOperation.identity()
    if identity not in reduced_symops:
        LOG.debug("Appending identity symop %s to reduced_symops")
        reduced_symops.append(identity)
    LOG.debug("Reduced symmetry list contains %d symops", len(reduced_symops))

    full_symops = []

    for symop in reduced_symops:
        full_symops.append(symop)
        for t in translations:
            full_symops.append(symop + t)

    if lattice_type > 0:
        full_symops += [x.inverted() for x in full_symops]

    LOG.debug("Expanded symmetry list contains %d symops", len(full_symops))
    return full_symops


def reduced_symmetry_list(full_symops, lattice_type):
    "1=P, 2=I, 3=rhombohedral obverse on hexagonal axes, 4=F, 5=A, 6=B, 7=C."
    output_symmetry_operations = set(full_symops)
    symop_codes = sorted(x.integer_code for x in full_symops)

    lattice_type_value = abs(lattice_type)
    translations = LATTICE_TYPE_TRANSLATIONS[lattice_type_value]

    reduced_symops = [SymmetryOperation.identity()]
    symops_to_process = list(full_symops)

    inversion = lattice_type > 0

    while symops_to_process:
        next_symop = symops_to_process.pop(0)
        if next_symop in reduced_symops:
            continue
        if inversion and next_symop.inverted() in reduced_symops:
            continue
        for t in translations:
            x = next_symop + t
            if inversion and x.inverted() in reduced_symops:
                break
            if x in reduced_symops:
                break
        else:
            reduced_symops.append(next_symop)

    LOG.debug("Reduced symmetry list contains %d symops", len(reduced_symops))
    return reduced_symops


class SpaceGroup:
    """Represent a crystallographic space group, including
    all necessary symmetry operations in fractional coordinates,
    the international tables number from 1-230, and the international
    tables symbol.
    """

    def __init__(self, international_tables_number, choice=""):
        if international_tables_number < 1 or international_tables_number > 230:
            raise ValueError("Space group number must be between [1, 230]")
        self.international_tables_number = international_tables_number
        if not choice:
            sgdata = SG_FROM_NUMBER[str(international_tables_number)][0]
        else:
            candidates = SG_FROM_NUMBER[str(international_tables_number)]
            for candidate in candidates:
                if choice == candidate.choice:
                    sgdata = candidate
                    break
            else:
                raise ValueError("Could not find choice {}".format(choice))
        self.symbol = sgdata.short
        self.full_symbol = sgdata.full
        self.choice = sgdata.choice
        self.centering = sgdata.centering
        self.point_group = sgdata.pointgroup
        self.schoenflies = sgdata.schoenflies
        self.centrosymmetric = sgdata.centrosymmetric
        symops = sgdata.symops
        self.symmetry_operations = [
            SymmetryOperation.from_integer_code(s) for s in symops
        ]

    @property
    def cif_section(self) -> str:
        "Representation of the SpaceGroup in CIF files"
        return "\n".join(
            "{} {}".format(i, sym.cif_form)
            for i, sym in enumerate(self.symmetry_operations, start=1)
        )

    @property
    def crystal_system(self):
        sg = self.international_tables_number
        if sg <= 0 or sg >= 231:
            raise ValueError("International spacegroup number must be between 1-230")
        if sg <= 2:
            return "triclinic"
        if sg <= 16:
            return "monoclinic"
        if sg <= 74:
            return "orthorhombic"
        if sg <= 142:
            return "tetragonal"
        if sg <= 167:
            return "trigonal"
        if sg <= 194:
            return "hexagonal"
        return "cubic"

    @property
    def lattice_type(self):
        latt = abs(self.latt)
        inum = self.international_tables_number
        if inum < 143 or inum > 194:
            return self.crystal_system
        if inum in (146, 148, 155, 160, 161, 166, 167):
            if self.choice == "H":
                return "hexagonal"
            elif self.choice == "R":
                return "rhombohedral"
        else:
            return "hexagonal"

    @property
    def latt(self) -> int:
        """
        >>> P1 = SpaceGroup(1)
        >>> P21c = SpaceGroup(14)
        >>> I41 = SpaceGroup(14)
        >>> R3bar = SpaceGroup(148)
        >>> P1.latt
        -1
        >>> P21c.latt
        1
        >>> R3bar.latt
        3
        """
        centering_to_latt = {
            "primitive": 1,  # P
            "body": 2,  # I
            "rcenter": 3,  # R
            "face": 4,  # F
            "aface": 5,  # A
            "bface": 6,  # B
            "cface": 7,  # C
        }
        if not self.centrosymmetric:
            return -centering_to_latt[self.centering]
        return centering_to_latt[self.centering]

    def __len__(self):
        return len(self.symmetry_operations)

    def ordered_symmetry_operations(self):
        # make sure we do the unit symop first
        unity = 0
        for i, s in enumerate(self.symmetry_operations):
            if s.is_identity():
                unity = i
                break
        else:
            return self.symmetry_operations
        other_symops = (
            self.symmetry_operations[:unity] + self.symmetry_operations[unity + 1 :]
        )
        return [self.symmetry_operations[unity]] + other_symops

    def apply_all_symops(self, coordinates: np.ndarray):
        """For a given set of coordinates, apply all symmetry
        operations in this space group, yielding a set subject
        to only translational symmetry (i.e. a unit cell).
        Assumes the input coordinates are fractional."""
        nsites = len(coordinates)
        transformed = np.empty((nsites * len(self), 3))
        generator_symop = np.empty(nsites * len(self), dtype=np.int32)

        # make sure we do the unit symop first
        unity = 0
        for i, s in enumerate(self.symmetry_operations):
            if s.integer_code == 16484:
                unity = i
                break
        transformed[0:nsites] = coordinates
        generator_symop[0:nsites] = 16484
        other_symops = (
            self.symmetry_operations[:unity] + self.symmetry_operations[unity + 1 :]
        )
        for i, s in enumerate(other_symops, start=1):
            transformed[i * nsites : (i + 1) * nsites] = s(coordinates)
            generator_symop[i * nsites : (i + 1) * nsites] = s.integer_code
        return generator_symop, transformed

    def __repr__(self):
        return "{} {}: {}".format(
            self.__class__.__name__, self.international_tables_number, self.symbol
        )

    def __eq__(self, other):
        return (
            self.international_tables_number == other.international_tables_number
        ) and (self.choice == other.choice)

    def __hash__(self):
        return hash((self.international_tables_number, self.choice))

    def reduced_symmetry_operations(self):
        return reduced_symmetry_list(self.symmetry_operations, self.latt)

    def subgroups(self):
        "This definition of subgroups is probably not correct"
        symop_ids = set([x.integer_code for x in self.symmetry_operations])
        subgroups = []
        for symops in SG_FROM_SYMOPS:
            if symop_ids.issuperset(symops):
                sgdata = SG_FROM_SYMOPS[symops]
                sg = SpaceGroup(sgdata.number, choice=sgdata.choice)
                if sg != self:
                    subgroups.append(sg)
        return subgroups

    @classmethod
    def from_symmetry_operations(cls, symops, expand_latt=None):
        """Find a matching spacegroup for a given set of symmetry
        operations, optionally treating them as a reduced set of
        symmetry operations and expanding them based on the lattice
        type."""
        if expand_latt is not None:
            if not -8 < expand_latt < 8:
                raise ValueError("expand_latt must be between [-7, 7]")
            symops = expanded_symmetry_list(symops, expand_latt)
        encoded = tuple(sorted(s.integer_code for s in symops))
        if encoded not in SG_FROM_SYMOPS:
            raise ValueError(
                "Could not find matching spacegroup for "
                "the following symops:\n{}".format(
                    "\n".join(str(s) for s in sorted(symops))
                )
            )
        else:
            sgdata = SG_FROM_SYMOPS[encoded]
            return SpaceGroup(sgdata.number, choice=sgdata.choice)