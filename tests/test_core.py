import os

import numpy as np
import pytest
from astropy.io import fits

from conftest import make_winered_like
from molecfit_gui.core import config as cfgmod
from molecfit_gui.core.backends import EsorexBackend, MolecfitJob, StandaloneBackend
from molecfit_gui.core.config import AppSettings, MolecfitConfig
from molecfit_gui.core.excludes import detect_aberrations, merge_ranges, read_ranges, write_ranges
from molecfit_gui.core.fits_io import detect_order, observation_time, read_spectrum
from molecfit_gui.core.normalize import continuum, fit_continuum, get_natural_cubic_spline_model
from molecfit_gui.core.pipeline import prepare_spectrum, process_file
from molecfit_gui.core.runner import CommandContext


def _sklearn_reference(x, y, n_knots):
    """Implementación original (sklearn) para comparar, si sklearn está disponible."""
    sk = pytest.importorskip("sklearn.linear_model")
    knots = np.linspace(x.min(), x.max(), n_knots + 2)[1:-1]
    n = len(knots)
    X = np.zeros((len(x), n - 1))
    X[:, 0] = x

    def d(i):
        return (np.maximum(0, x - knots[i]) ** 3 - np.maximum(0, x - knots[n - 1]) ** 3) / (knots[n - 1] - knots[i])
    for i in range(n - 2):
        X[:, i + 1] = d(i) - d(n - 2)
    return sk.LinearRegression().fit(X, y).predict(X)


def test_spline_matches_original_formulation():
    rng = np.random.default_rng(1)
    x = np.linspace(9700, 13200, 3000)
    y = 1 + 0.1 * np.sin(x / 300) + rng.normal(0, 0.01, x.size)
    ours = get_natural_cubic_spline_model(x, y, x.min(), x.max(), n_knots=15).predict(x)
    try:
        ref = _sklearn_reference(x, y, 15)
    except pytest.skip.Exception:
        # sin sklearn: comparar contra lstsq directo sin reescalar
        knots = np.linspace(x.min(), x.max(), 17)[1:-1]
        from molecfit_gui.core.normalize import NaturalCubicSpline
        A = np.column_stack([np.ones_like(x), NaturalCubicSpline(knots=knots).transform(x)])
        ref = A @ np.linalg.lstsq(A, y, rcond=None)[0]
    assert np.allclose(ours, ref, rtol=1e-6, atol=1e-6)


def test_continuum_normalizes_to_one(tmp_path):
    wave, flux, cont = make_winered_like(tmp_path / "a_m43_x.fits")
    norm, fit, w_ex, f_ex = continuum(wave, flux, low_rej=1.8, high_re=0.0, niter=10, order=3)
    top = np.percentile(norm, 90)
    assert 0.97 < top < 1.03
    assert len(w_ex) > 0           # rechazó líneas de absorción
    assert np.all(np.isfinite(norm))


def test_continuum_no_rejection_and_nan():
    x = np.linspace(0, 1, 200)
    y = 2 + x
    y[5] = np.nan
    r = fit_continuum(x, y, low_rej=0, high_rej=0, niter=3, n_knots=4)
    assert np.isnan(r.normalized_flux[5])
    assert np.allclose(np.delete(r.normalized_flux, 5), 1, atol=1e-8)


def test_read_and_prepare(tmp_path):
    src = tmp_path / "star9_m45_spec.fits"
    wave, flux, _ = make_winered_like(src)
    s = read_spectrum(src)
    assert np.allclose(s.wave, wave)
    assert detect_order(src) == "45"
    mjd, secs = observation_time(s.header)
    assert abs(secs - (3 * 3600 + 25 * 60 + 41.5)) < 1e-3
    out, res = prepare_spectrum(str(src), MolecfitConfig(), str(tmp_path / "p.fits"))
    h = fits.getheader(out)
    assert h["CD1_1"] == pytest.approx(0.1)
    assert h["MJD-OBS"] == pytest.approx(mjd)
    assert h["DATE-OBS"] == "2023-07-15"  # no se sobrescribe
    assert np.allclose(read_spectrum(out).wave, wave)


def test_excludes_roundtrip(tmp_path):
    w = np.linspace(1.2, 1.21, 1000)
    f = np.ones_like(w)
    f[400:410] = 10
    r = detect_aberrations(w, f, 2.0)
    assert r is not None and r[0] < w[400] < w[409] < r[1]
    assert detect_aberrations(w, np.ones_like(w)) is None
    p = write_ranges(tmp_path / "x.dat", [r, (1.0, 1.1), (1.05, 1.15)])
    assert merge_ranges(read_ranges(p))[0] == (1.0, 1.15)


def test_rc_parameters_use_none_for_missing_keys(tmp_path):
    src = tmp_path / "s_m50_.fits"
    make_winered_like(src)
    prep, _ = prepare_spectrum(str(src), MolecfitConfig(), str(tmp_path / "p.fits"))
    job = MolecfitJob(prep, str(tmp_path / "wd"), MolecfitConfig(), exclude=[(1.2, 1.21)])
    p = EsorexBackend(AppSettings()).model_parameters(job, fits.getheader(prep), (1.2, 1.22))
    assert p["WAVE_INCLUDE"] == "1.200000,1.220000"   # sin rangos → todo el espectro
    assert p["LIST_MOLEC"] == "H2O,CO2,CH4,O2"
    assert p["FIT_MOLEC"] == "1,1,1,1"
    assert p["WLG_TO_MICRON"] == "0.0001"
    assert p["WAVE_EXCLUDE"] == "1.200000,1.210000"
    assert p["TELESCOPE_ANGLE_KEYWORD"] == "ALTITUDE"
    assert p["MIRROR_TEMPERATURE_KEYWORD"] == "NONE"      # T_M1AIR no está en el header
    assert p["OBSERVING_DATE_KEYWORD"] == "MJD-OBS"
    assert p["USE_ONLY_INPUT_PRIMARY_DATA"] == "FALSE"
    assert p["COLUMN_LAMBDA"] == "lambda" and p["COLUMN_FLUX"] == "flux"


def test_par_template_clone(tmp_path):
    tpl = tmp_path / "tpl.par"
    tpl.write_text("# comentario\nfilename: viejo.fits\nfit_res_box: 0\nplot_creation: none\n\nend\n")
    out = StandaloneBackend.write_par(str(tmp_path / "n.par"),
                                      {"filename": "nuevo.fits", "cont_n": "4"}, str(tpl))
    txt = open(out).read()
    assert "filename: nuevo.fits" in txt and "viejo" not in txt
    assert "plot_creation: none" in txt and "cont_n: 4" in txt
    assert txt.strip().endswith("end") and txt.count("end") == 1


def test_wsl_path_translation():
    ctx = CommandContext(prefix="wsl -e")
    assert ctx.path(r"D:\limpieza_espectros\a b.fits") == "/mnt/d/limpieza_espectros/a b.fits"
    assert CommandContext().path(r"D:\x") == r"D:\x"


def test_profiles_roundtrip(tmp_path):
    c = MolecfitConfig(profile_name="Mi perfil", wave_include=[(1.1, 1.2)])
    cfgmod.save_profile(c)
    loaded = cfgmod.list_profiles()["Mi perfil"]
    assert loaded.wave_include == [(1.1, 1.2)]
    assert loaded.molecules[0].name == "H2O"


def test_full_pipeline_with_fake_esorex(tmp_path, fake_esorex):
    src = tmp_path / "in" / "star1_m44_x.fits"
    src.parent.mkdir()
    make_winered_like(src)
    settings = AppSettings(esorex_path=fake_esorex)
    ok, msg = EsorexBackend(settings).check()
    assert ok, msg
    logs = []
    r = process_file(str(src), MolecfitConfig(), settings, str(tmp_path / "out"), log=logs.append)
    assert r.ok, (r.message, "\n".join(logs))
    assert r.best_fit["gaussfwhm"] == 4.2
    assert os.path.exists(r.files["corrected_image"])
    assert os.path.exists(r.files["figure"])
    R = r.resolution(cdelt=0.1, wlg_to_micron=1e-4)
    assert 25000 < R < 35000
    corr = read_spectrum(r.files["corrected_image"])
    assert len(corr.flux) == 2048


def test_standalone_backend_with_fake_binaries(tmp_path):
    """Flujo .par → molecfit → calctrans → *_tac.fits (como molecfit_pipeline_2.py)."""
    import sys
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "molecfit").write_text(f"#!{sys.executable}\nimport sys; print('molecfit', sys.argv[1])\n")
    (bindir / "calctrans").write_text(f"""#!{sys.executable}
import sys, numpy as np
from astropy.io import fits
par = dict(l.split(':', 1) for l in open(sys.argv[1]) if ':' in l and not l.startswith('#'))
par = {{k.strip(): v.strip() for k, v in par.items()}}
h = fits.open(par['filename']); f = h[0].data.astype(float); hd = h[0].header
lam = (hd['CRVAL1'] + np.arange(len(f)) * hd['CDELT1']) * float(par['wlgtomicron'])
t = np.full_like(f, 0.9)
cols = [fits.Column(name=n, format='D', array=a) for n, a in (('LAMBDA', lam), ('FLUX', f), ('MTRANS', t), ('CFLUX', f / t))]
fits.HDUList([fits.PrimaryHDU(), fits.BinTableHDU.from_columns(cols)]).writeto(
    par['output_dir'] + '/' + par['output_name'] + '_tac.fits', overwrite=True)
open(par['output_dir'] + '/' + par['output_name'] + '_fit.res', 'w').write('FWHM of Gaussian in pixels: 4.1\\n')
""")
    for f in bindir.iterdir():
        f.chmod(0o755)
    src = tmp_path / "s_m47_x.fits"
    make_winered_like(src)
    settings = AppSettings(backend="standalone", standalone_bin_dir=str(bindir))
    assert StandaloneBackend(settings).check()[0]
    r = process_file(str(src), MolecfitConfig(), settings, str(tmp_path / "out"), log=lambda s: None)
    assert r.ok, r.message
    assert r.best_fit["gaussfwhm"] == 4.1
    assert np.allclose(np.nanmedian(r.transmission), 0.9)
    par = open(tmp_path / "out" / "molecfit" / "s_m47_x" / "s_m47_x_norm.par").read()
    assert "list_molec: H2O CO2 CH4 O2" in par and par.strip().endswith("end")
    assert len(read_spectrum(r.files["corrected_image"]).flux) == 2048


def test_esorex_exit0_without_products_is_error(tmp_path, fake_esorex):
    """Como en 4.4.2 real: 'No range values defined' sale con código 0 y sin productos."""
    src = tmp_path / "s_m48_x.fits"
    make_winered_like(src)
    txt = open(fake_esorex).read()
    anchor = "recipe, sof = pos[0], pos[1]\n"
    assert anchor in txt
    txt = txt.replace(anchor, anchor + 'if recipe == "molecfit_model":\n'
                      '    print("[ ERROR ] molecfit_model: No range values defined"); sys.exit(0)\n')
    open(fake_esorex, "w").write(txt)
    r = process_file(str(src), MolecfitConfig(), AppSettings(esorex_path=fake_esorex),
                     str(tmp_path / "out"), log=lambda s: None)
    assert not r.ok
    assert "No range values defined" in r.message and "BEST_FIT_PARAMETERS" in r.message
