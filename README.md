# Molecfit GUI

Interfaz gráfica en Python 3 (PySide6) para la **corrección telúrica con Molecfit** y la
**normalización de espectros**. Nace del flujo usado en la tesis *Análisis espectroscópico del
cúmulo globular Gran 1* (WINERED), y reemplaza a la antigua GUI de Molecfit, que dependía de
Python 2.7.

- **Dos backends:**
  - **Molecfit 4.4.2** (EsoRex), la versión con soporte de ESO. Ejecuta `molecfit_model` →
    `molecfit_calctrans` → `molecfit_correct`.
  - **Molecfit 1.5.9 standalone**, la que se usó en la tesis. Ejecuta `molecfit` → `calctrans` con
    un `.par`, y puede partir de una plantilla `.par` como hacía `archivo_par_2`.
- **Normalización** con spline cúbico natural y rechazo σ iterativo (port de `natural_cubic_spline.py`).
- **Perfiles de instrumento** en JSON. Incluye WINERED / Magellan Clay (Las Campanas) con los
  valores de `molecfit_pipeline_2.py`.
- **Procesamiento por lotes** de todos los órdenes de echelle, con log en vivo y cancelación.
- **Resultados:** espectro corregido, curva de transmisión, parámetros del ajuste y estimación de
  la resolución `R = λ/(FWHM·CDELT)` (ecs. 2.3–2.4 de la tesis).
- **Zonas de exclusión:** detección automática (port de `generar_excludes`) o selección con el ratón,
  y re-ejecución del orden.
- **Uso sin GUI:** `molecfit-gui-batch` o `import molecfit_gui.core` desde un notebook.

## Instalación

### Windows (con WSL2)
Molecfit no tiene versión para Windows, así que todo corre dentro de WSL2 (Ubuntu). La ventana se
ve como una aplicación normal de Windows gracias a WSLg (Windows 11, o Windows 10 actualizado).

1. Haz doble clic en `install\install_windows.cmd`. Hace lo siguiente:
   - comprueba WSL2 (si falta, indica cómo instalarlo);
   - instala las dependencias de Ubuntu y crea el entorno Python;
   - crea el acceso directo **Molecfit GUI** en el Escritorio.
2. Para instalar también Molecfit 4.4.2 desde el kit que ya descargaste en WSL:
   ```powershell
   powershell -ExecutionPolicy Bypass -File install\install_windows.ps1 -MolecfitKit ~/molecfit-kit-4.4.2-6
   ```

### Linux / WSL / macOS
```bash
bash install/install.sh --system-deps                                         # GUI + dependencias
bash install/install.sh --with-molecfit=~/molecfit-kit-4.4.2-6                # + Molecfit desde el kit
bash install/install.sh --with-molecfit                                       # + Molecfit vía Homebrew
```
El entorno queda en `~/.local/share/molecfit_gui/venv` y los comandos `molecfit-gui` y
`molecfit-gui-batch` en `~/.local/bin`.

Para comprobar Molecfit:
```bash
molecfit-gui-batch --check
```

## Uso

1. **Datos.** Elige la carpeta con los FITS (un orden por archivo, por ejemplo `..._m43_...fits`) y la
   carpeta de salida. Desde WSL, `D:\...` se ve como `/mnt/d/...`.
2. **Normalización.** Ajusta knots, iteraciones y umbrales de rechazo, y revisa la vista previa.
   - Valores de `molecfit_pipeline_2.py`: 3 knots, 10 iteraciones, low_rej 1.8.
   - Valores de la Fig. 2.1 de la tesis: 15/15.
3. **Molecfit.** Elige el backend y el perfil, revisa los parámetros y pulsa **Ejecutar marcados**.
   El botón *Verificar instalación* comprueba que existan `esorex` y las recetas.
4. **Resultados.** Para cada orden se muestran el espectro original, el corregido y la transmisión.
   Si ves aberraciones, márcalas (con *Detectar aberraciones* o con el ratón) y pulsa
   **Re-ejecutar este orden**.

### Estructura de la salida
```
salida/
├── prepared/    FITS normalizados con MJD-OBS, UTC-SEC y CD1_1 (entrada de Molecfit)
├── molecfit/    una carpeta por orden: .rc/.sof o .par, logs y productos de Molecfit
├── corrected/   <nombre>_tac.fits: espectro corregido como imagen 1D (mismo WCS)
├── figures/     figura de diagnóstico de 4 paneles por orden
└── excludes/    <nombre>_exclude.dat: zonas excluidas (µm), mismo formato que la tesis
```

### Desde un notebook
```python
from molecfit_gui.core import AppSettings, MolecfitConfig, process_file
r = process_file("star9_m45.fits", MolecfitConfig(), AppSettings(), "salida/")
r.wave_um, r.flux_corr, r.transmission, r.best_fit
```

## Equivalencia de parámetros (.par 1.5.9 → .rc 4.4.2)

| tesis (`archivo_par_2`) | 4.4.2 | valor WINERED |
|---|---|---|
| `list_molec` / `fit_molec` | `LIST_MOLEC` / `FIT_MOLEC` (separados por comas) | H2O,CO2,CH4,O2 / 1,1,1,1 |
| `wlgtomicron` | `WLG_TO_MICRON` | 1e-4 (Å) |
| `vac_air` | `WAVELENGTH_FRAME` | VAC |
| `wrange_exclude` (.dat) | `WAVE_EXCLUDE` (pares separados por comas, µm) | desde `excludes/` |
| `cont_n` / `wlc_n` | `CONTINUUM_N` / `WLC_N` | 4 / 4 |
| `res_gauss`, `kernfac` | `RES_GAUSS`, `KERNFAC` | 3.7 px, 300 |
| `obsdate_key: DATE-OBS` (con MJD) | `OBSERVING_DATE_KEYWORD=MJD-OBS` | se añade al header sin tocar DATE-OBS |
| `utc_key: UT` (segundos) | `UTC_KEYWORD=UTC-SEC` | se añade al header |
| `geoelev`, `longitude`, `latitude` | `ELEVATION_VALUE`, `LONGITUDE_VALUE`, `LATITUDE_VALUE` | 2375 m, −70.69239°, −29.01418° |

Si un keyword del perfil (por ejemplo `T_M1AIR`) no existe en el header, se usa `NONE` y el valor fijo
del perfil, para que Molecfit no falle.

## Notas sobre Molecfit 4.4.2 (verificadas con WINERED)
- El espectro se entrega a Molecfit como **tabla FITS** (`SCIENCE_TABLE.fits`, columnas `lambda` y
  `flux`). Con una imagen 1D, `molecfit_model` falla con *Not all input data are in the header!*.
- 4.4.2 **exige al menos un rango** en `WAVE_INCLUDE`; si no se definen, la GUI usa el orden completo.
- `molecfit_calctrans` y `molecfit_correct` necesitan los mapeos `MAPPING_ATMOSPHERIC`,
  `MAPPING_CONVOLVE` y `MAPPING_CORRECT` (aquí `0,1`), aunque el manual los presenta como opcionales.
- esorex puede terminar con código 0 sin crear productos; la GUI comprueba los archivos esperados
  después de cada receta.

## Pendiente de verificar
- `molecfit_calctrans` y `molecfit_correct` con datos reales (tras añadir los mapeos).
- **1.5.9:** algunas claves (`relcol`, `fit_back`, `ref_atm`) se escriben según la nomenclatura de esa
  versión. Si no se usa una plantilla `.par`, conviene revisar el primer `.par` generado.
- **Kernel boxcar:** el perfil WINERED lo deja desactivado (`FIT_RES_BOX=FALSE`, `RES_BOX=0`).

## Desarrollo
```bash
pip install -e .[dev]
QT_QPA_PLATFORM=offscreen pytest
```
Las pruebas usan un `esorex` falso que imita las salidas de las recetas, así que no hace falta
tener Molecfit instalado.

## Citas
Si usas Molecfit en una publicación, cita Smette et al. 2015 (A&A 576, A77) y Kausch et al. 2015
(A&A 576, A78).
