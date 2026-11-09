"""Perfiles de instrumento/observatorio y parámetros de Molecfit.

Un ``MolecfitConfig`` reúne lo que en ``archivo_par_2`` estaba fijo en el
código. Cada backend lo traduce a su formato (.par para 1.5.9, .rc para 4.4.2).
Los perfiles se guardan como JSON en ``~/.config/molecfit_gui/profiles``.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    d = Path(base) / "molecfit_gui"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Molecule:
    name: str
    fit: bool = True
    rel_col: float = 1.0


@dataclass
class NormalizationConfig:
    low_rej: float = 1.8
    high_rej: float = 0.0
    niter: int = 10
    n_knots: int = 3


@dataclass
class MolecfitConfig:
    # --- perfil ---
    profile_name: str = "WINERED (Magellan Clay)"
    # --- datos de entrada ---
    wlg_to_micron: float = 1e-4          # Å → µm
    wavelength_frame: str = "VAC"        # VAC | AIR
    transmission: bool = True
    default_error: float = 0.01
    # --- moléculas ---
    molecules: list[Molecule] = field(default_factory=lambda: [
        Molecule("H2O"), Molecule("CO2"), Molecule("CH4"), Molecule("O2")])
    # --- rangos (µm); vacío = sin rangos ---
    wave_include: list[tuple[float, float]] = field(default_factory=list)
    # --- ajuste ---
    ftol: float = 1e-5
    xtol: float = 1e-5
    fit_continuum: bool = True
    continuum_n: int = 4
    continuum_const: float = 1.0
    fit_wlc: bool = True
    wlc_n: int = 4
    wlc_const: float = 0.0
    # --- kernel (LSF) ---
    fit_res_box: bool = False
    res_box: float = 0.0
    fit_res_gauss: bool = True
    res_gauss: float = 3.7
    fit_res_lorentz: bool = False
    res_lorentz: float = 0.0
    kernmode: bool = False
    kernfac: float = 300.0
    varkern: bool = False
    # --- keywords de header / valores fijos ---
    obsdate_key: str = "MJD-OBS"
    utc_key: str = "UTC-SEC"
    telalt_key: str = "ALTITUDE"
    telalt_value: float = 90.0
    rhum_key: str = "OUT-HUM"
    rhum_value: float = 15.0
    pres_key: str = "OUT-PRS"
    pres_value: float = 750.0
    temp_key: str = "OUT-TMP"
    temp_value: float = 15.0
    m1temp_key: str = "T_M1AIR"
    m1temp_value: float = 15.0
    # --- observatorio (Las Campanas) ---
    elevation: float = 2375.0
    longitude: float = -70.69239
    latitude: float = -29.01418
    slit_width: float = 0.29
    pix_scale: float = 0.144
    # --- atmósfera ---
    reference_atmosphere: str = "equ.fits"
    gdas_profile: str = "auto"
    pwv: float = -1.0
    # --- otros ---
    expert_extra: dict = field(default_factory=dict)  # parámetros extra tal cual
    normalization: NormalizationConfig = field(default_factory=NormalizationConfig)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        d["wave_include"] = [list(r) for r in self.wave_include]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "MolecfitConfig":
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        if "molecules" in d:
            d["molecules"] = [m if isinstance(m, Molecule) else Molecule(**m) for m in d["molecules"]]
        if "wave_include" in d:
            d["wave_include"] = [tuple(r) for r in d["wave_include"]]
        if "normalization" in d and isinstance(d["normalization"], dict):
            d["normalization"] = NormalizationConfig(**d["normalization"])
        return cls(**d)

    def save(self, path: str | os.PathLike) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | os.PathLike) -> "MolecfitConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class AppSettings:
    """Ajustes de la aplicación (rutas de ejecutables, carpetas)."""
    backend: str = "esorex"                       # esorex | standalone
    esorex_path: str = "esorex"
    standalone_bin_dir: str = ""                  # carpeta con molecfit y calctrans
    standalone_par_template: str = ""             # .par de plantilla opcional
    command_prefix: str = ""                      # p.ej. "wsl -e" si la GUI corre en Windows
    omp_threads: int = 1
    last_input_dir: str = ""
    last_output_dir: str = ""

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "AppSettings":
        p = cls.path()
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in data.items() if k in known})
            except (ValueError, TypeError):
                pass
        return cls()

    def save(self) -> None:
        self.path().write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def profiles_dir() -> Path:
    d = config_dir() / "profiles"
    d.mkdir(exist_ok=True)
    return d


def builtin_profiles() -> dict[str, MolecfitConfig]:
    winered = MolecfitConfig()
    generic = MolecfitConfig(
        profile_name="Genérico (Paranal, keywords ESO)",
        wlg_to_micron=1.0,
        obsdate_key="MJD-OBS", utc_key="UTC",
        telalt_key="ESO TEL ALT", rhum_key="ESO TEL AMBI RHUM",
        pres_key="ESO TEL AMBI PRES START", temp_key="ESO TEL AMBI TEMP",
        m1temp_key="ESO TEL TH M1 TEMP",
        elevation=2635.0, longitude=-70.4051, latitude=-24.6276,
        slit_width=0.4, pix_scale=0.086,
        continuum_n=1, wlc_n=1, res_gauss=1.0, kernfac=3.0,
        ftol=1e-10, xtol=1e-10,
    )
    return {winered.profile_name: winered, generic.profile_name: generic}


def list_profiles() -> dict[str, MolecfitConfig]:
    out = builtin_profiles()
    for p in sorted(profiles_dir().glob("*.json")):
        try:
            cfg = MolecfitConfig.load(p)
            out[cfg.profile_name] = cfg
        except (ValueError, TypeError, KeyError):
            continue
    return out


def save_profile(cfg: MolecfitConfig) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in cfg.profile_name)
    p = profiles_dir() / f"{safe}.json"
    cfg.save(p)
    return p
