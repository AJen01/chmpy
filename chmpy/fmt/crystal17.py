from chmpy.templates import load_template
from pathlib import Path
import logging

LOG = logging.getLogger(__name__)
CRYSTAL17_TEMPLATE = load_template("crystal17")


def to_crystal17_input(crystal, **kwargs):
    space_group = crystal.space_group.international_tables_number
    params = crystal.uc.parameters if crystal.space_group.lattice_type == "triclinic" else crystal.uc.unique_parameters_deg
    method = kwargs.get("method", "hf-3c")
    if method == "hf-3c":
        method = "HF3C\nRESCALES8\n0.70"
        kwargs["basis_set"] = "MINIX"
    else:
        method = "DFT\n" + method.upper()
    parameters = {
        "basis_set_keywords": kwargs.get("basis_set_keywords", {}),
        "shrink_factors": kwargs.get("shrink_factors", (4, 4)),
        "iflag": kwargs.get("iflag", 0),
        "ifhr": 1 if crystal.space_group.lattice_type == "rhombohedral" else 0,
        "ifso": 0, # change of origin
        "space_group": space_group,
        "cell_parameters": " ".join(f"{x:10.6f}" for x in params),
        "basis_set": kwargs.get("basis_set", "cc-pVDZ"),
    }
    return CRYSTAL17_TEMPLATE.render(
        title=crystal.titl,
        method=method,
        natoms=len(crystal.asym),
        atoms=zip(crystal.asym.positions, crystal.asym.elements),
        **parameters,
    )


def load_crystal17_output_string(string):
    total_energy_line = ""
    for line in string.splitlines():
        if "TOTAL ENERGY" in line:
            total_energy_line = line
    energy = float(total_energy_line.split(")")[-1].split()[0])
    return energy

def load_crystal17_output_file(filename):
    return load_crystal17_output_string(Path(filename).read_text())