#!/usr/bin/env bash
# Instalador de Molecfit GUI para Linux, WSL2 (Windows) y macOS.
#
#   bash install/install.sh [opciones]
#
# Opciones:
#   --system-deps            instala librerías del sistema (Qt, compiladores) con apt/dnf/brew
#   --with-molecfit[=KIT]    instala Molecfit 4.4.2: con KIT = carpeta del molecfit-kit
#                            (ejecuta su install_pipeline); sin KIT usa Homebrew (esopipe-molecfit)
#   --venv DIR               dónde crear el entorno Python (por defecto ~/.local/share/molecfit_gui/venv)
#   --no-shortcut            no crear acceso directo en el menú de aplicaciones
#   -y, --yes                no preguntar
#   -h, --help               esta ayuda
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${HOME}/.local/share/molecfit_gui/venv"
BIN_DIR="${HOME}/.local/bin"
SYSTEM_DEPS=0
WITH_MOLECFIT=0
MOLECFIT_KIT=""
SHORTCUT=1
YES=0

c_ok=$'\e[32m'; c_warn=$'\e[33m'; c_err=$'\e[31m'; c_off=$'\e[0m'
info() { printf '%s==>%s %s\n' "$c_ok" "$c_off" "$*"; }
warn() { printf '%s[aviso]%s %s\n' "$c_warn" "$c_off" "$*"; }
die()  { printf '%s[error]%s %s\n' "$c_err" "$c_off" "$*" >&2; exit 1; }
ask()  { [[ $YES == 1 ]] && return 0; read -r -p "$1 [s/N] " r; [[ $r =~ ^[sSyY]$ ]]; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --system-deps) SYSTEM_DEPS=1 ;;
    --with-molecfit) WITH_MOLECFIT=1 ;;
    --with-molecfit=*) WITH_MOLECFIT=1; MOLECFIT_KIT="${1#*=}" ;;
    --venv) VENV="$2"; shift ;;
    --no-shortcut) SHORTCUT=0 ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "opción desconocida: $1" ;;
  esac
  shift
done

# ---------------------------------------------------------------- plataforma
OS="$(uname -s)"
IS_WSL=0
if [[ $OS == Linux ]] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then IS_WSL=1; fi
case "$OS" in
  Linux)  PLAT=$([[ $IS_WSL == 1 ]] && echo "WSL2 (Linux en Windows)" || echo "Linux") ;;
  Darwin) PLAT="macOS" ;;
  *) die "Sistema no soportado: $OS. En Windows usa install\\install_windows.ps1" ;;
esac
info "Plataforma: $PLAT"
info "Código fuente: $SRC_DIR"

PM=""
command -v apt-get >/dev/null 2>&1 && PM=apt
[[ -z $PM ]] && command -v dnf >/dev/null 2>&1 && PM=dnf
[[ -z $PM ]] && command -v brew >/dev/null 2>&1 && PM=brew

# ---------------------------------------------------------------- dependencias del sistema
check_system_python() {
  # En Ubuntu, apt y command-not-found necesitan el python3 de la distribución (módulo apt_pkg).
  if ls /usr/lib/python3/dist-packages/apt_pkg*.so >/dev/null 2>&1; then
    if ! /usr/bin/python3 -c 'import apt_pkg' >/dev/null 2>&1; then
      warn "/usr/bin/python3 apunta a $(readlink -f /usr/bin/python3), que no es el Python de Ubuntu."
      warn "Eso rompe apt y command-not-found. Para repararlo (Ubuntu 22.04 usa 3.10):"
      warn "    sudo ln -sf python3.10 /usr/bin/python3"
      warn "Molecfit GUI no lo necesita: usa su propio entorno virtual."
    fi
  fi
}

if [[ $SYSTEM_DEPS == 1 ]]; then
  case "$PM" in
    apt)
      info "Instalando dependencias con apt (requiere sudo)…"
      # 'apt-get update' puede devolver error por scripts ajenos (p.ej. command-not-found roto
      # cuando /usr/bin/python3 no es el Python del sistema); no es fatal para instalar paquetes.
      sudo apt-get update || warn "apt-get update terminó con errores (ver arriba); continúo."
      check_system_python
      sudo apt-get install -y python3 python3-venv python3-pip \
        libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 \
        libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
        libxcb-xkb1 libdbus-1-3 libfontconfig1 fonts-dejavu-core \
        build-essential gfortran zlib1g-dev libcurl4-openssl-dev ;;
    dnf)
      info "Instalando dependencias con dnf (requiere sudo)…"
      sudo dnf install -y python3 python3-pip mesa-libGL mesa-libEGL libxkbcommon-x11 \
        xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil \
        dbus-libs fontconfig gcc gcc-gfortran make zlib-devel libcurl-devel ;;
    brew)
      info "Instalando dependencias con Homebrew…"
      brew install python@3.12 ;;
    *) warn "No se reconoce el gestor de paquetes; instala Python ≥ 3.9 y las librerías de Qt manualmente." ;;
  esac
fi

# ---------------------------------------------------------------- Python
# Se elige el primer Python ≥ 3.9 que tenga venv + ensurepip (los de deadsnakes a veces no los traen).
PY=""
SEEN_NO_VENV=""
for cand in python3.10 python3.12 python3.11 python3.13 python3.9 python3; do
  command -v "$cand" >/dev/null 2>&1 || continue
  "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || continue
  if "$cand" -c 'import venv, ensurepip' >/dev/null 2>&1; then
    PY="$(command -v "$cand")"; break
  fi
  SEEN_NO_VENV="$SEEN_NO_VENV $cand"
done
if [[ -z $PY ]]; then
  [[ -n $SEEN_NO_VENV ]] && die "Hay Python ($SEEN_NO_VENV) pero sin venv. En Ubuntu: sudo apt install python3-venv (o python3.X-venv)."
  die "Se necesita Python ≥ 3.9 (vuelve a ejecutar con --system-deps)."
fi
info "Python: $PY ($("$PY" --version 2>&1))"

# ---------------------------------------------------------------- entorno virtual
if [[ -d $VENV ]]; then
  info "Actualizando entorno existente en $VENV"
else
  info "Creando entorno virtual en $VENV"
  mkdir -p "$(dirname "$VENV")"
  "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip wheel >/dev/null
info "Instalando molecfit-gui y dependencias (numpy, astropy, matplotlib, PySide6)…"
"$VENV/bin/python" -m pip install --upgrade "$SRC_DIR"

# ---------------------------------------------------------------- lanzadores
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/molecfit-gui" "$BIN_DIR/molecfit-gui"
ln -sf "$VENV/bin/molecfit-gui-batch" "$BIN_DIR/molecfit-gui-batch"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    RC="$HOME/.bashrc"; [[ $OS == Darwin ]] && RC="$HOME/.zshrc"
    if ! grep -q 'molecfit_gui PATH' "$RC" 2>/dev/null; then
      printf '\n# molecfit_gui PATH\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
      warn "Se añadió ~/.local/bin al PATH en $RC (abre una terminal nueva)."
    fi ;;
esac

if [[ $SHORTCUT == 1 && $OS == Linux ]]; then
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  cat > "$APPS/molecfit-gui.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Molecfit GUI
Comment=Corrección telúrica con Molecfit y normalización de espectros
Exec=$BIN_DIR/molecfit-gui
Terminal=false
Categories=Science;Astronomy;Education;
EOF
  info "Acceso directo creado (en WSL aparece en el menú Inicio de Windows como 'Molecfit GUI (Ubuntu)')."
fi

# ---------------------------------------------------------------- Molecfit
if [[ $WITH_MOLECFIT == 1 ]]; then
  if [[ -n $MOLECFIT_KIT ]]; then
    KIT="${MOLECFIT_KIT/#\~/$HOME}"   # bash no expande ~ en --opcion=~/ruta
    KIT="${KIT%/}"
    if [[ -f $KIT && $KIT == *.tar.gz ]]; then
      info "Descomprimiendo $KIT"
      tar -xzf "$KIT" -C "$(dirname "$KIT")"
      KIT="${KIT%.tar.gz}"
    fi
    [[ -d $KIT ]] || die "No existe la carpeta del kit: $KIT"
    [[ -f $KIT/install_pipeline ]] || die "No se encontró $KIT/install_pipeline (contenido: $(ls "$KIT" | tr '\n' ' '))"
    [[ -x $KIT/install_pipeline ]] || { warn "install_pipeline no era ejecutable; aplicando chmod +x"; chmod +x "$KIT/install_pipeline"; }
    info "Ejecutando el instalador de ESO (te pedirá el directorio de instalación)…"
    ( cd "$KIT" && ./install_pipeline 2>&1 | tee "$HOME/molecfit_install.log" )
    info "Log de la compilación: ~/molecfit_install.log"
    warn "Añade la carpeta bin/ que elegiste al PATH, p.ej.:  echo 'export PATH=\$HOME/eso/pipelines/bin:\$PATH' >> ~/.bashrc"
  elif command -v brew >/dev/null 2>&1; then
    info "Instalando Molecfit con Homebrew (eso/pipelines)…"
    brew tap eso/pipelines
    brew trust --tap eso/pipelines 2>/dev/null || true
    brew install esopipe-molecfit
  else
    warn "Para instalar Molecfit pasa la carpeta del kit: --with-molecfit=~/molecfit-kit-4.4.2-6"
    warn "o instala Homebrew (https://brew.sh) y vuelve a ejecutar con --with-molecfit."
  fi
fi

# ---------------------------------------------------------------- verificación
info "Verificando…"
"$VENV/bin/python" -c "import molecfit_gui, PySide6, astropy; print('  molecfit_gui', molecfit_gui.__version__, '| PySide6', PySide6.__version__, '| astropy', astropy.__version__)"
if command -v esorex >/dev/null 2>&1; then
  "$VENV/bin/molecfit-gui-batch" --check || true
else
  warn "esorex no está en el PATH. Puedes indicar su ruta en la GUI: Configuración → Ejecutables."
fi
if [[ $IS_WSL == 1 && -z ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]]; then
  warn "No se detecta WSLg (sin DISPLAY). En PowerShell ejecuta 'wsl --update' y reinicia WSL ('wsl --shutdown')."
fi

echo
info "Listo. Abre la interfaz con:  molecfit-gui"
info "Procesamiento por lotes sin GUI:  molecfit-gui-batch CARPETA_ENTRADA CARPETA_SALIDA"
