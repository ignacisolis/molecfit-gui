"""Núcleo sin GUI: se puede usar desde notebooks o scripts."""
from .config import AppSettings, MolecfitConfig, Molecule, NormalizationConfig  # noqa: F401
from .fits_io import read_spectrum, write_spectrum, list_fits, detect_order  # noqa: F401
from .normalize import continuum, fit_continuum  # noqa: F401
from .pipeline import process_file, prepare_spectrum  # noqa: F401
