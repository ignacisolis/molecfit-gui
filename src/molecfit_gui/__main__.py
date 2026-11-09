"""``python -m molecfit_gui`` abre la interfaz gráfica."""
import sys


def run() -> int:
    try:
        from .gui.main_window import main
    except ImportError as exc:  # PySide6 o matplotlib ausentes
        print(f"No se pudo iniciar la GUI ({exc}).\n"
              "Ejecuta el instalador (install/install.sh) o: pip install PySide6 matplotlib astropy",
              file=sys.stderr)
        return 1
    return main()


if __name__ == "__main__":
    sys.exit(run())
