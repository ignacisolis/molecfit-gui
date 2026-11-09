# Molecfit GUI - instalador para Windows (usa WSL2 + Ubuntu)
#
# Uso (PowerShell):
#   powershell -ExecutionPolicy Bypass -File install\install_windows.ps1 [-Distro Ubuntu] [-MolecfitKit ~/molecfit-kit-4.4.2-6]
#
# Pasos:
#   1. Comprueba que WSL2 este instalado (si no, ofrece instalarlo; requiere administrador y reinicio).
#   2. Ejecuta install/install.sh dentro de la distribucion (crea el entorno Python y los lanzadores).
#   3. Crea un acceso directo "Molecfit GUI" en el Escritorio que abre la interfaz via WSLg.
param(
    [string]$Distro = "",
    [string]$MolecfitKit = "",
    [switch]$SkipSystemDeps,
    [switch]$NoShortcut
)
$ErrorActionPreference = "Stop"

function Info($m) { Write-Host "==> $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[aviso] $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. WSL
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
$distros = @()
if ($wsl) {
    $raw = (& wsl.exe -l -q 2>$null) -join "`n"
    $distros = ($raw -replace "`0", "") -split "`r?`n" | Where-Object { $_.Trim() -ne "" } | ForEach-Object { $_.Trim() }
}
if (-not $wsl -or $distros.Count -eq 0) {
    Warn "WSL2 con una distribucion Linux no esta instalado."
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Warn "Abre PowerShell como Administrador y ejecuta:  wsl --install -d Ubuntu"
        Warn "Reinicia, abre Ubuntu una vez para crear tu usuario y vuelve a ejecutar este instalador."
        exit 1
    }
    $r = Read-Host "Instalar WSL2 + Ubuntu ahora? (s/N)"
    if ($r -match '^[sSyY]$') {
        wsl.exe --install -d Ubuntu
        Warn "Reinicia Windows, abre 'Ubuntu' para crear tu usuario y vuelve a ejecutar este instalador."
    }
    exit 1
}
if (-not $Distro) {
    $Distro = ($distros | Where-Object { $_ -like "Ubuntu*" } | Select-Object -First 1)
    if (-not $Distro) { $Distro = $distros[0] }
}
Info "Distribucion WSL: $Distro"
& wsl.exe --update 2>$null | Out-Null   # asegura WSLg (ventanas Linux en Windows)

# ---------------------------------------------------------------- 2. install.sh dentro de WSL
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$wslRoot = (& wsl.exe -d $Distro -- wslpath -a "$root").Trim()
Info "Proyecto (visto desde WSL): $wslRoot"

$argsSh = @()
if (-not $SkipSystemDeps) { $argsSh += "--system-deps" }
if ($MolecfitKit) { $argsSh += "--with-molecfit=$MolecfitKit" }
if ($NoShortcut) { $argsSh += "--no-shortcut" }
$cmd = "cd '$wslRoot' && bash install/install.sh $($argsSh -join ' ')"
Info "Ejecutando en WSL: $cmd"
& wsl.exe -d $Distro -- bash -lc $cmd
if ($LASTEXITCODE -ne 0) { throw "install.sh termino con error ($LASTEXITCODE)" }

# ---------------------------------------------------------------- 3. acceso directo
if (-not $NoShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desktop "Molecfit GUI.lnk"
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = "$env:WINDIR\System32\wsl.exe"
    $s.Arguments = "-d $Distro -- bash -lc `"`$HOME/.local/bin/molecfit-gui`""
    $s.WindowStyle = 7   # minimizado: solo se ve la ventana de la GUI
    $s.Description = "Molecfit GUI (WSL)"
    $s.Save()
    Info "Acceso directo creado: $lnk"
}

Info "Listo. Abre 'Molecfit GUI' desde el Escritorio o el menu Inicio."
Info "Tus datos en D:\ se ven desde la GUI como /mnt/d/..."
