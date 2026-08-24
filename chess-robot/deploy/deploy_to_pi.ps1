<#
.SYNOPSIS
  Transfiere el proyecto a la Raspberry Pi y, opcionalmente, lo instala alla.

.DESCRIPTION
  Copia todo el arbol chess-robot/ (codigo, docs, configuracion calibrada y el
  frontend ya buildeado) por SSH, sin depender de git: van tambien los archivos
  que todavia no estan commiteados.

  Se excluyen .git, node_modules, .venv, __pycache__ y los datos de la
  instalacion (backend/data/, puntajes de la feria).

.EXAMPLE
  # Solo copiar los archivos (la Pi ya tiene el entorno armado)
  .\deploy_to_pi.ps1 -PiHost 192.168.0.10

.EXAMPLE
  # Primera vez: copiar + crear venv, instalar dependencias y el servicio
  .\deploy_to_pi.ps1 -PiHost 192.168.0.10 -Install -RobotHost 192.168.0.25

.EXAMPLE
  # Actualizar codigo y reiniciar el backend en la Pi
  .\deploy_to_pi.ps1 -PiHost 192.168.0.10 -Restart
#>
param(
  [string]$PiHost = "192.168.0.10",
  [string]$User = "esteban",
  # Ruta destino en la Pi, relativa al home del usuario.
  [string]$Dest = "robot-ajedrez/chess-robot",
  # Variables de entorno del servicio systemd (solo se usan con -Install).
  [string]$Driver = "vision",
  [string]$RobotHost = "192.168.0.25",
  [string]$Difficulty = "intermedio",
  # No rebuildear el frontend antes de copiar (usa el dist/ actual).
  [switch]$NoBuild,
  # No pisar backend/config/ de la Pi (si la calibracion vive alla).
  [switch]$KeepConfig,
  # Crear venv, instalar dependencias del sistema y el servicio systemd.
  [switch]$Install,
  # Reiniciar chess-backend al terminar la copia.
  [switch]$Restart
)

# Los errores reales de los comandos nativos se detectan por $LASTEXITCODE:
# con "Stop", cualquier linea de stderr (un warning de tar, por ejemplo)
# abortaria el deploy.
$ErrorActionPreference = "Continue"

$projectDir = Split-Path -Parent $PSScriptRoot          # ...\chess-robot
$parentDir  = Split-Path -Parent $projectDir            # ...\Robot Ajedrez
$leaf       = Split-Path -Leaf $projectDir              # chess-robot
$target     = "$User@$PiHost"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

# Ejecuta un script bash en la Pi: lo sube a /tmp con finales de linea LF (lo
# que se rompe si se manda por stdin desde PowerShell) y lo corre alla.
# -Interactive reserva TTY para que sudo pueda pedir la contrasena.
function Invoke-RemoteScript {
  param([string]$Script, [string[]]$Arguments = @(), [switch]$Interactive)

  foreach ($a in $Arguments) {
    if ($a -match "'") { Fail "Argumento remoto invalido (comillas simples): $a" }
  }
  # Nombre unico por paso: con un nombre fijo, un script corto escrito sobre
  # uno mas largo puede terminar ejecutando restos del anterior (bash recibe
  # lineas sueltas del paso previo, con errores de sintaxis y, peor, comandos
  # destructivos fuera de contexto).
  $stamp  = [guid]::NewGuid().ToString("N").Substring(0, 8)
  $remote = "/tmp/chess-remote-step-$stamp.sh"
  $local  = Join-Path $env:TEMP "chess-remote-step-$stamp.sh"
  $body = $Script -replace "`r`n", "`n"
  [IO.File]::WriteAllText($local, $body, (New-Object Text.UTF8Encoding $false))
  & scp -q $local ($target + ":$remote")
  if ($LASTEXITCODE -ne 0) { Fail "No se pudo copiar el script remoto" }
  Remove-Item $local -Force

  $quoted = ($Arguments | ForEach-Object { "'" + $_ + "'" }) -join " "
  $cmd = "bash $remote $quoted; rc=`$?; rm -f $remote; exit `$rc"
  # LogLevel=ERROR: con -t, ssh escribe "Connection to ... closed." en stderr y
  # PowerShell lo muestra como si fuera un error del deploy.
  if ($Interactive) { & ssh -t -o LogLevel=ERROR $target $cmd } else { & ssh $target $cmd }
  if ($LASTEXITCODE -ne 0) { Fail "El paso remoto fallo (codigo $LASTEXITCODE)" }
}

# --------------------------------------------------------------- 1. Conexion
Step "Probando SSH contra $target"
$probe = & ssh -o ConnectTimeout=6 -o BatchMode=yes $target "echo ok; uname -m" 2>&1
if ($LASTEXITCODE -ne 0) {
  Write-Host $probe
  Write-Host "Sugerencias:" -ForegroundColor Yellow
  Write-Host "  - Verifica que la Pi este encendida y en la red: ping $PiHost"
  Write-Host "  - Confirma la IP en la Pi con: hostname -I"
  Write-Host "  - Si pide contrasena, copia tu clave publica a la Pi con ssh-copy-id"
  Fail "No se pudo abrir SSH a $target"
}
Write-Host ($probe -join " ")

# ------------------------------------------------------- 2. Build del frontend
if (-not $NoBuild) {
  Step "Buildeando el frontend (para no necesitar npm en la Pi)"
  Push-Location (Join-Path $projectDir "frontend")
  try {
    if (-not (Test-Path "node_modules")) { & npm install }
    & npm run build
    if ($LASTEXITCODE -ne 0) { Fail "npm run build fallo" }
  } finally { Pop-Location }
}
if (-not (Test-Path (Join-Path $projectDir "frontend\dist\index.html"))) {
  Fail "No existe frontend/dist - corre sin -NoBuild para generarlo"
}

# ------------------------------------------------------------ 3. Empaquetado
Step "Empaquetando el proyecto"
$tarball = Join-Path $env:TEMP "chess-robot-deploy.tgz"
if (Test-Path $tarball) { Remove-Item $tarball -Force }

$excludes = @(
  "--exclude=*/.git", "--exclude=*/.git/*",
  "--exclude=*/node_modules", "--exclude=*/node_modules/*",
  "--exclude=*/.venv", "--exclude=*/.venv/*",
  "--exclude=*/__pycache__", "--exclude=*/__pycache__/*",
  "--exclude=*/.pytest_cache", "--exclude=*/.pytest_cache/*",
  "--exclude=*.pyc",
  "--exclude=*/backend/data", "--exclude=*/backend/data/*"
)
if ($KeepConfig) {
  $excludes += "--exclude=*/backend/config"
  $excludes += "--exclude=*/backend/config/*"
}

& tar -czf $tarball -C $parentDir @excludes $leaf
if ($LASTEXITCODE -ne 0) { Fail "tar fallo" }
$sizeMb = [math]::Round((Get-Item $tarball).Length / 1MB, 1)
Write-Host "  $tarball - $sizeMb MB"

# ----------------------------------------------------------------- 4. Copia
Step "Copiando a ${target}:~/$Dest"
& scp -q $tarball ($target + ":/tmp/chess-robot-deploy.tgz")
if ($LASTEXITCODE -ne 0) { Fail "scp fallo" }

$unpack = @'
set -e
dest="$HOME/$1"
mkdir -p "$dest"
# Un deploy anterior pudo dejar directorios 0555 (ver mas abajo): sin esto no
# se puede escribir dentro de ellos.
chmod -R u+rwX "$dest"

# dist/ es 100% generado y sus assets llevan hash en el nombre: si no se borra,
# se acumulan los bundles de todos los deploys anteriores.
rm -rf "$dest/frontend/dist"

# Copia de seguridad de la calibracion que ya viva en la Pi.
if [ -d "$dest/backend/config" ]; then
  bak="$dest/backend/config.bak-$(date +%Y%m%d-%H%M%S)"
  cp -r "$dest/backend/config" "$bak"
  echo "  config anterior respaldada en $bak"
fi

# El tar de Windows (bsdtar) guarda los directorios como 0555 porque Windows no
# tiene permisos POSIX. Si se extrae directo sobre el destino, GNU tar aplica
# ese modo a cada carpeta y despues no puede escribir adentro. Por eso se
# extrae a un staging, se normalizan los permisos y recien ahi se copia.
# --warning: los headers SCHILY de bsdtar no le interesan a GNU tar.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
# --delay-directory-restore: sin esto tar aplica el 0555 a cada carpeta apenas
# la crea y ya no puede seguir escribiendo adentro.
tar --delay-directory-restore --warning=no-unknown-keyword \
    -xzf /tmp/chess-robot-deploy.tgz -C "$tmp" --strip-components=1
chmod -R u+rwX,go+rX "$tmp"
cp -a "$tmp/." "$dest/"
rm -f /tmp/chess-robot-deploy.tgz
# El kiosk se sirve desde frontend/dist y mas arriba se borro el viejo: si el
# nuevo no llego, /ui devuelve 404 y la pantalla de la expo queda en blanco.
# Abortar aca evita ademas reiniciar el backend sobre una instalacion rota.
if [ ! -f "$dest/frontend/dist/index.html" ]; then
  echo "  ERROR: frontend/dist no llego a $dest" >&2
  exit 1
fi
# cp no borra: los archivos eliminados en el repo quedan en la Pi hasta que se
# limpien a mano (no molestan, pero conviene saberlo).
echo "  desempaquetado en $dest ($(find "$dest" -type f | wc -l) archivos)"
'@
Invoke-RemoteScript -Script $unpack -Arguments @($Dest)

# La especificacion vive un nivel arriba del proyecto (el README la enlaza como
# ../PROYECTO_ROBOT_AJEDREZ.md), asi que se copia al padre del destino.
$spec = Join-Path $parentDir "PROYECTO_ROBOT_AJEDREZ.md"
if (Test-Path $spec) {
  $specDest = "~/" + (Split-Path -Parent $Dest.Replace("\", "/")).Replace("\", "/")
  if ($specDest -eq "~/") { $specDest = "~" }
  & scp -q $spec ($target + ":" + $specDest + "/PROYECTO_ROBOT_AJEDREZ.md")
  if ($LASTEXITCODE -ne 0) { Fail "No se pudo copiar PROYECTO_ROBOT_AJEDREZ.md" }
  Write-Host "  PROYECTO_ROBOT_AJEDREZ.md copiado"
}

# ------------------------------------------------------------ 5. Instalacion
if ($Install) {
  Step "Instalando entorno y servicio en la Pi (pide sudo)"
  $installScript = @'
set -e
dest="$HOME/$1"; svc_user="$2"; driver="$3"; robot="$4"; level="$5"

sudo apt-get update
# fonts-noto-color-emoji: sin ella los emoji de la UI salen como cuadraditos.
# build-essential/cmake/boost: ur_rtde se compila desde fuente en aarch64.
sudo apt-get install -y python3-venv python3-dev stockfish \
  fonts-noto-color-emoji build-essential cmake \
  libboost-system-dev libboost-thread-dev

cd "$dest/backend"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
# ur_rtde no tiene wheel aarch64: se compila desde fuente (~25 min). /tmp es
# tmpfs, o sea RAM: compilar ahi le roba memoria al sistema (4 GB, sin swap) y
# se pierde todo si la Pi se reinicia. Y sin limitar el paralelismo la Pi queda
# al borde de quedarse sin memoria.
mkdir -p "$HOME/.build-tmp"
export TMPDIR="$HOME/.build-tmp"
export CMAKE_BUILD_PARALLEL_LEVEL=3
export MAKEFLAGS=-j3
# requirements-pi.txt incluye requirements.txt + snap7/gpiod/ur_rtde.
.venv/bin/pip install -r requirements-pi.txt

# Unit systemd generado con el usuario y las rutas reales de esta Pi.
sudo tee /etc/systemd/system/chess-backend.service >/dev/null <<UNIT
[Unit]
Description=Chess Robot backend (FastAPI + vision + UR10e)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$svc_user
WorkingDirectory=$dest/backend
# systemd no hereda el PATH del login: sin esto no se encuentra stockfish.
Environment=PATH=/usr/games:/usr/local/bin:/usr/bin:/bin
Environment=CHESS_DRIVER=$driver
Environment=CHESS_ROBOT_HOST=$robot
Environment=CHESS_DIFFICULTY=$level
# Margen para que el UR complete el pick & place a velocidad reducida antes
# de dar la jugada por fallida y pasar a resync (default del codigo: 10 s).
Environment=CHESS_ROBOT_VERIFY_TIMEOUT=45
ExecStart=$dest/backend/.venv/bin/python -m app.api.server
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now chess-backend
sleep 2
systemctl --no-pager --lines=15 status chess-backend || true
'@
  Invoke-RemoteScript -Script $installScript -Interactive `
    -Arguments @($Dest, $User, $Driver, $RobotHost, $Difficulty)
}
elseif ($Restart) {
  Step "Reiniciando chess-backend"
  $restartScript = "sudo systemctl restart chess-backend; sleep 2; systemctl --no-pager --lines=10 status chess-backend || true"
  Invoke-RemoteScript -Script $restartScript -Interactive
}

Remove-Item $tarball -Force -ErrorAction SilentlyContinue

Step "Listo"
Write-Host "La Pi sirve todo en http://${PiHost}:8000" -ForegroundColor Green
Write-Host "  /ui           partida (kiosk)"
Write-Host "  /calibracion  calibracion de vision"
Write-Host "  /calibration  calibracion del robot"
Write-Host "  /             diagnostico de sensores"
