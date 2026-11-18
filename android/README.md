# EVO·VISION — APK (WebView wrapper)

Wrapper Android do HMI EVO·VISION (site servido em `http://<LAN>:8000/`).

## O que o app faz
- Abre o HMI fullscreen (tema escuro, sem barra do navegador)
- **Tentativa automática de reconexão** quando o servidor está fora do ar
- **Configurar servidor sem reinstalar:** toque e segure (long-press) na tela e edite o endereço
  - O padrão de fábrica é `10.20.30.83:8000` (desta máquina no Wi-Fi atual)
  - Para outro PC/rede, ajuste nas settings (ex.: `http://192.168.0.10:8000/`)
- Botão Voltar navega dentro do histórico do site
- Tela de offline com retry automático a cada 5 s

## Requisitos para compilar
- JDK 17+ (instalado via `winget install Microsoft.OpenJDK.21`)
- Android SDK cmdline-tools, platform android-34, build-tools 34.0.0
- Gradle 8.7+

## Build
```sh
export ANDROID_HOME="$LOCALAPPDATA/Android/Sdk"
export PATH="$USERPROFILE/gradle/gradle-8.7/bin:$PATH"
gradle :app:assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

## Instalar no celular
1. Copie o APK para o telefone (cabo/USB ou e-mail)
2. Abra o arquivo no celular e permita "instalar de fontes desconhecidas"
3. Mantenha o PC e o celular na **mesma rede Wi-Fi**
4. Abra o app EVO·VISION — o HMI carrega automaticamente

## Limitações honestas
- O botão **WEB CAM** do site usa `getUserMedia`, que exige HTTPS — não funciona via HTTP no WebView.
  Use a aba **MONITOR** para ver a câmera do PC ao vivo (é o cenário principal).
- O campo **server URL** aceita IP ou hostname da LAN; HTTPS funciona se o site for servido com certificado.
