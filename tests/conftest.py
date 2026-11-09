import os
import sys
import textwrap

import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))


def make_winered_like(path, order=43, n=2048, lam0=12000.0, step=0.1, seed=0):
    """Espectro sintético con continuo curvo + líneas estelares + 'telúricas'."""
    rng = np.random.default_rng(seed)
    pix = np.arange(n)
    wave = lam0 + pix * step
    x = (wave - wave.mean()) / np.ptp(wave)
    cont = 5000 * (1 + 0.3 * x - 0.4 * x ** 2)
    lines = np.ones(n)
    for c in rng.uniform(wave[50], wave[-50], 25):
        lines -= 0.4 * np.exp(-0.5 * ((wave - c) / 0.4) ** 2)
    flux = cont * lines * (1 + rng.normal(0, 0.005, n))
    hdr = fits.Header()
    hdr["CRVAL1"] = lam0
    hdr["CDELT1"] = step
    hdr["CRPIX1"] = 1.0
    hdr["CTYPE1"] = "LINEAR"
    hdr["DATE-OBS"] = "2023-07-15"
    hdr["UT"] = "03:25:41.500"
    hdr["ALTITUDE"] = 62.5
    hdr["OUT-HUM"] = 12.0
    hdr["OUT-PRS"] = 770.0
    hdr["OUT-TMP"] = 9.5
    fits.PrimaryHDU(flux.astype(np.float32), header=hdr).writeto(path, overwrite=True)
    return wave, flux, cont


FAKE_ESOREX = textwrap.dedent(r'''
    #!{python}
    """esorex falso: imita las salidas de las recetas de molecfit 4.4.2."""
    import sys, os, numpy as np
    from astropy.io import fits
    args = sys.argv[1:]
    if "--recipes" in args:
        print("molecfit_model\nmolecfit_calctrans\nmolecfit_correct"); sys.exit(0)
    out = [a.split("=",1)[1] for a in args if a.startswith("--output-dir=")][0]
    rc = [a.split("=",1)[1] for a in args if a.startswith("--recipe-config=")][0]
    pos = [a for a in args if not a.startswith("--")]
    recipe, sof = pos[0], pos[1]
    frames = dict(reversed(l.split()) for l in open(sof) if l.strip())
    sci = frames["SCIENCE"]
    rcp = dict(l.strip().split("=", 1) for l in open(rc) if "=" in l)
    h = fits.open(sci); hdr = h[0].header
    # como 4.4.2: con USE_ONLY_INPUT_PRIMARY_DATA=FALSE se lee la tabla de la extensión 1
    assert rcp["USE_ONLY_INPUT_PRIMARY_DATA"] == "FALSE" and h[0].data is None
    tab = h[1].data
    if recipe == "molecfit_model":
        assert rcp["COLUMN_LAMBDA"] == "lambda" and "MJD-OBS" in hdr
        assert rcp["WAVE_INCLUDE"] != "NULL", "4.4.2 exige rangos"
        lam_col, flux_col = rcp["COLUMN_LAMBDA"], rcp["COLUMN_FLUX"]
    else:
        lam_col, flux_col = "lambda", "flux"
    # como 4.4.2 real: sin mapeos, calctrans/correct fallan con error(14)
    if recipe == "molecfit_calctrans" and not (rcp.get("MAPPING_ATMOSPHERIC") and rcp.get("MAPPING_CONVOLVE")):
        print("[ ERROR ] molecfit_calctrans: Recipe failed!, error(14)="); sys.exit(0)
    if recipe == "molecfit_correct" and not rcp.get("MAPPING_CORRECT"):
        print("[ ERROR ] molecfit_correct: Recipe failed!, error(14)="); sys.exit(0)
    flux = np.asarray(tab[flux_col], float)
    lam = np.asarray(tab[lam_col], float) * 1e-4
    trans = 1 - 0.3*np.exp(-0.5*((lam-lam.mean())/2e-4)**2)
    print("running", recipe, "with", open(rc).read().count("="), "params")
    def table(path, **cols):
        fits.BinTableHDU.from_columns([fits.Column(name=k, format="D", array=v) for k,v in cols.items()]).writeto(path, overwrite=True)
    if recipe == "molecfit_model":
        assert "SCIENCE" in frames
        for f in ("ATM_PARAMETERS", "MODEL_MOLECULES"):
            table(os.path.join(out, f + ".fits"), x=np.zeros(3))
        c1 = fits.Column(name="parameter", format="20A", array=["gaussfwhm", "Status", "rel_mol_col_H2O"])
        c2 = fits.Column(name="value", format="D", array=[4.2, 1.0, 0.8])
        c3 = fits.Column(name="uncertainty", format="D", array=[0.1, -1, 0.01])
        fits.BinTableHDU.from_columns([c1, c2, c3]).writeto(os.path.join(out, "BEST_FIT_PARAMETERS.fits"), overwrite=True)
    elif recipe == "molecfit_calctrans":
        for f in ("ATM_PARAMETERS", "MODEL_MOLECULES", "BEST_FIT_PARAMETERS"):
            assert os.path.exists(frames[f]), f
        table(os.path.join(out, "TELLURIC_DATA.fits"), **{"lambda": lam, "mtrans": trans, "cflux": flux/trans})
        fits.PrimaryHDU(trans).writeto(os.path.join(out, "TELLURIC_CORR.fits"), overwrite=True)
    elif recipe == "molecfit_correct":
        name = os.path.basename(sci)
        cols = [fits.Column(name="lambda", format="D", array=tab["lambda"]),
                fits.Column(name="flux", format="D", array=flux/trans)]
        fits.HDUList([fits.PrimaryHDU(header=hdr), fits.BinTableHDU.from_columns(cols)]).writeto(
            os.path.join(out, "SCIENCE_TELLURIC_CORR_" + name), overwrite=True)
''')


@pytest.fixture
def fake_esorex(tmp_path):
    p = tmp_path / "bin" / "esorex"
    p.parent.mkdir()
    p.write_text(FAKE_ESOREX.lstrip().replace("{python}", sys.executable))
    p.chmod(0o755)
    return str(p)
