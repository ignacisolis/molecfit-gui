"""Uso por línea de comandos (sin GUI), útil para lotes o servidores.

    molecfit-gui-batch ENTRADA SALIDA [--profile perfil.json] [--backend esorex|standalone]
                       [--no-normalize] [--check]
"""
from __future__ import annotations

import argparse
import os
import sys

from .core.backends import make_backend
from .core.config import AppSettings, MolecfitConfig, list_profiles
from .core.fits_io import list_fits
from .core.pipeline import process_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="molecfit-gui-batch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("entrada", nargs="?", help="archivo FITS o carpeta con FITS")
    ap.add_argument("salida", nargs="?", help="carpeta de salida")
    ap.add_argument("--profile", help="perfil JSON o nombre de perfil guardado")
    ap.add_argument("--backend", choices=["esorex", "standalone"])
    ap.add_argument("--esorex", help="ruta a esorex")
    ap.add_argument("--bin-dir", help="carpeta con molecfit/calctrans (1.5.9)")
    ap.add_argument("--no-normalize", action="store_true")
    ap.add_argument("--check", action="store_true", help="solo verificar la instalación")
    a = ap.parse_args(argv)

    settings = AppSettings.load()
    if a.backend:
        settings.backend = a.backend
    if a.esorex:
        settings.esorex_path = a.esorex
    if a.bin_dir:
        settings.standalone_bin_dir = a.bin_dir

    ok, msg = make_backend(settings).check()
    print(("OK: " if ok else "PROBLEMA: ") + msg)
    if a.check:
        return 0 if ok else 1
    if not a.entrada or not a.salida:
        ap.error("se requieren ENTRADA y SALIDA")
    if not ok:
        return 1

    if a.profile and os.path.exists(a.profile):
        cfg = MolecfitConfig.load(a.profile)
    else:
        profiles = list_profiles()
        cfg = profiles.get(a.profile) if a.profile else next(iter(profiles.values()))
        if cfg is None:
            print(f"Perfil no encontrado: {a.profile}. Disponibles: {', '.join(profiles)}")
            return 1
    files = list_fits(a.entrada) if os.path.isdir(a.entrada) else [a.entrada]
    n_ok = 0
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {os.path.basename(f)}")
        try:
            r = process_file(f, cfg, settings, a.salida, normalize=not a.no_normalize)
            n_ok += int(r.ok)
        except Exception as exc:  # seguir con el resto, como aplicar_todo_todos
            print(f"Error procesando {f}: {exc}")
    print(f"\nTerminado: {n_ok}/{len(files)} correctos. Resultados en {a.salida}")
    return 0 if n_ok == len(files) else 2


if __name__ == "__main__":
    sys.exit(main())
