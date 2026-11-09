"""Ejecución de procesos externos con log en vivo, cancelación y traducción de rutas.

Si la GUI corre en Windows y Molecfit en WSL, se configura
``command_prefix="wsl -e"``: los comandos se lanzan como ``wsl -e esorex ...`` y
las rutas ``D:\\carpeta`` se traducen a ``/mnt/d/carpeta`` dentro de los
argumentos y de los archivos .sof/.par.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

LogFn = Callable[[str], None]

_WIN_DRIVE = re.compile(r"^([A-Za-z]):[\\/](.*)$")


class Cancelled(Exception):
    pass


@dataclass
class CommandContext:
    prefix: str = ""
    env: dict | None = None

    @property
    def uses_wsl(self) -> bool:
        p = self.prefix.strip().lower()
        return p.startswith("wsl") or "wsl.exe" in p

    def path(self, p: str | os.PathLike) -> str:
        """Ruta tal como la verá el ejecutable (traduce a /mnt/x/ si hay WSL)."""
        s = str(p)
        if self.uses_wsl:
            m = _WIN_DRIVE.match(s)
            if m:
                return f"/mnt/{m.group(1).lower()}/" + m.group(2).replace("\\", "/")
        return s

    def argv(self, args: list[str]) -> list[str]:
        prefix = shlex.split(self.prefix, posix=(os.name != "nt")) if self.prefix.strip() else []
        return prefix + list(args)

    def which(self, exe: str) -> str | None:
        if self.uses_wsl:
            try:
                out = subprocess.run(self.argv(["bash", "-lc", f"command -v {shlex.quote(exe)}"]),
                                     capture_output=True, text=True, timeout=30)
                return out.stdout.strip() or None
            except (OSError, subprocess.TimeoutExpired):
                return None
        if os.path.isabs(exe) or os.sep in exe:
            return exe if os.path.isfile(exe) and os.access(exe, os.X_OK) else None
        return shutil.which(exe)


def run_command(args: list[str], ctx: CommandContext, cwd: str | None = None,
                log: LogFn | None = None, cancel: threading.Event | None = None,
                log_file: str | None = None) -> int:
    """Ejecuta ``args`` transmitiendo stdout+stderr línea a línea."""
    argv = ctx.argv(args)
    log = log or (lambda s: None)
    log(f"$ {' '.join(shlex.quote(a) for a in argv)}")
    env = os.environ.copy()
    if ctx.env:
        env.update({k: str(v) for k, v in ctx.env.items()})
    fh = open(log_file, "a", encoding="utf-8") if log_file else None
    try:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1,
                                errors="replace")
    except FileNotFoundError as exc:
        msg = f"No se encontró el ejecutable: {argv[0]} ({exc})"
        log(msg)
        if fh:
            fh.write(msg + "\n")
            fh.close()
        return 127
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            log(line)
            if fh:
                fh.write(line + "\n")
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Cancelled()
        return proc.wait()
    finally:
        if fh:
            fh.close()
