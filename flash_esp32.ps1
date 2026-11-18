# ============================================================================
# flash_esp32.ps1 - Grava o firmware EVOVISION no ESP32 conectado (1 clique)
#
# O que faz:
#   1. Verifica/baixa o arduino-cli (nao precisa instalar Arduino IDE)
#   2. Garante o core ESP32 + bibliotecas (baixa se faltar)
#   3. Detecta automaticamente a porta USB do ESP32
#   4. Pergunta Wi-Fi (SSID/senha) e usa o DEVICE_TOKEN do .env
#   5. Compila e grava
#
# Uso: clique duplo em flash_esp32.bat (ou rode este script no PowerShell)
# ============================================================================
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host "  EVO.VISION - Gravar firmware no ESP32" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host ""

# ---------- 1) arduino-cli ----------
$tools = Join-Path $env:LOCALAPPDATA "evo-vision\tools"
$cliDir = Join-Path $tools "arduino-cli"
$cli = Join-Path $cliDir "arduino-cli.exe"
if (-not (Test-Path $cli)) {
    $onPath = Get-Command arduino-cli -ErrorAction SilentlyContinue
    if ($onPath) { $cli = $onPath.Source } else { $cli = $null }
}
if (-not $cli -or -not (Test-Path $cli)) {
    Write-Host "[1/6] Baixando arduino-cli (so na primeira vez)..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path $cliDir | Out-Null
    $zip = Join-Path $env:TEMP "arduino-cli.zip"
    Invoke-WebRequest -Uri "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip" -OutFile $zip
    Expand-Archive -Force $zip $cliDir
    $cli = Join-Path $cliDir "arduino-cli.exe"
} else {
    Write-Host "[1/6] arduino-cli OK: $cli" -ForegroundColor Green
}

# ---------- 2) core ESP32 + libs ----------
Write-Host "[2/6] Verificando core ESP32 (pode baixar ~300 MB na primeira vez)..." -ForegroundColor Yellow
& $cli config init --overwrite 2>&1 | Out-Null
& $cli config add board_manager.additional_urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json 2>&1 | Out-Null
& $cli core update-index 2>&1 | Out-Null
& $cli core install esp32:esp32 2>&1 | Out-Null
& $cli lib install "ArduinoJson" "OneWire" "DallasTemperature" 2>&1 | Out-Null
Write-Host "[2/6] Core e bibliotecas OK" -ForegroundColor Green

# ---------- 3) Porta USB do ESP32 ----------
Write-Host "[3/6] Detectando ESP32 na USB..." -ForegroundColor Yellow
$candidates = Get-PnpDevice -PresentOnly -Class Ports |
    Where-Object {
        $_.FriendlyName -notmatch "Bluetooth" -and
        $_.FriendlyName -match '(CP210|CH340|CH9102|FTDI|Prolific|Silicon Labs|USB Serial|USB-Serial|USB2.0)'
    }
if (-not $candidates -or $candidates.Count -eq 0) {
    Write-Host ""
    Write-Host "Nenhum ESP32 detectado na USB." -ForegroundColor Red
    Write-Host "Verifique:"
    Write-Host "  - Cabo USB DATA (nao apenas de carga)"
    Write-Host "  - Placa ligada (LED)"
    Write-Host "  - Driver CP210x/CH340 (Windows Update costuma instalar)"
    Write-Host "Portas seriais atuais:"
    Get-PnpDevice -PresentOnly -Class Ports | Format-Table FriendlyName, InstanceId -AutoSize | Out-String | Write-Host
    Read-Host "Pressione ENTER para sair"
    exit 1
}
$port = $null
if ($candidates.Count -eq 1) {
    $port = $candidates[0]
    Write-Host "[3/6] ESP32 detectado: $($port.FriendlyName)" -ForegroundColor Green
} else {
    Write-Host "Varias portas candidatas; escolha:"
    for ($i = 0; $i -lt $candidates.Count; $i++) {
        Write-Host "  [$i] $($candidates[$i].FriendlyName)"
    }
    $sel = Read-Host "Numero da porta"
    $port = $candidates[[int]$sel]
}
$com = ([regex]::Match($port.FriendlyName, '\((COM\d+)\)')).Groups[1].Value
if (-not $com) { Write-Host "Nao consegui extrair o COM de '$($port.FriendlyName)'." -ForegroundColor Red; exit 1 }
Write-Host "[3/6] Usando $com" -ForegroundColor Green

# ---------- 4) Wi-Fi + token ----------
Write-Host "[4/6] Parametros de rede (ficam SO nesta maquina e no firmware):" -ForegroundColor Yellow
$ssid = Read-Host "SSID do Wi-Fi"
$passSecure = Read-Host "Senha do Wi-Fi" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($passSecure)
$pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
$envFile = Join-Path $repoRoot ".env"
$deviceToken = ""
if (Test-Path $envFile) {
    $line = Select-String -Path $envFile -Pattern '^DEVICE_TOKEN=' | Select-Object -First 1
    if ($line) { $deviceToken = ($line.Line -split '=', 2)[1].Trim() }
}
if (-not $deviceToken) { $deviceToken = Read-Host "DEVICE_TOKEN (do .env ou da Vercel)" }
$deviceToken = $deviceToken.Trim()
if (-not $deviceToken) { Write-Host "DEVICE_TOKEN ausente - abortando." -ForegroundColor Red; exit 1 }
Write-Host "[4/6] DEVICE_TOKEN: $($deviceToken.Substring(0,8))... (nao exibido)" -ForegroundColor Green

# ---------- 5) Compilar com os parametros ----------
Write-Host "[5/6] Compilando firmware (~30-60s)..." -ForegroundColor Yellow
$buildDir = Join-Path $env:TEMP ("evo-firmware-" + [guid]::NewGuid().ToString("n"))
$sketchDir = Join-Path $buildDir "esp32_cloud_agent"
New-Item -ItemType Directory -Force -Path $sketchDir | Out-Null
$ino = Get-Content (Join-Path $repoRoot "esp32_cloud_agent.ino") -Raw
$ino = $ino.Replace('"SEU_WIFI_SSID"', "`"$ssid`"").Replace('"SEU_WIFI_SENHA"', "`"$pass`"").Replace('"COLE_O_DEVICE_TOKEN_DA_VERCEL_AQUI"', "`"$deviceToken`"")
Set-Content (Join-Path $sketchDir "esp32_cloud_agent.ino") $ino -Encoding UTF8
& $cli compile --fqbn esp32:esp32:esp32 $sketchDir
if ($LASTEXITCODE -ne 0) { Write-Host "Falha na compilacao (veja acima)." -ForegroundColor Red; exit 1 }
Write-Host "[5/6] Compilado OK" -ForegroundColor Green

# ---------- 6) Gravar ----------
Write-Host "[6/6] Gravando em $com (mantenha o cabo conectado)..." -ForegroundColor Yellow
& $cli upload --fqbn esp32:esp32:esp32 --port $com $sketchDir
if ($LASTEXITCODE -ne 0) {
    Write-Host "Falha na gravacao. Tente: segure o botao BOOT do ESP32, rode de novo e solte apos 'Connecting...'" -ForegroundColor Red
    exit 1
}
Write-Host ""
Write-Host "==============================================" -ForegroundColor Green
Write-Host "  FIRMWARE GRAVADO COM SUCESSO no $com" -ForegroundColor Green
Write-Host "==============================================" -ForegroundColor Green
Write-Host "O ESP32 agora aparece na nuvem em ~2s (heartbeat)."
Write-Host "Verifique em qualquer lugar: https://evolution-beta-six.vercel.app (Temperatura: fonte 'device')"
Write-Host "Serial: $cli monitor -p $com -c baudrate=115200"
Write-Host ""
Read-Host "Pressione ENTER para sair"
