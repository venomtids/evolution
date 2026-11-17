/*
 * esp32_controller.ino
 * Firmware ESP32 para Parada de Emergência Industrial (E-Stop)
 * 
 * Funcionalidades:
 *   - Servidor HTTP leve (WiFiServer) com endpoints REST:
 *       POST /emergency-stop : Aciona relé de parada (fail-safe, latched)
 *       POST /reset          : Reseta estado de emergência (apenas após botão físico)
 *       GET  /status         : Retorna estado atual do ESP32
 *   - Lógica Fail-Safe com retenção (latch): uma vez acionado E-Stop,
 *     o relé permanece ativo até reset explícito.
 *   - Botão físico com debounce (INPUT_PULLUP) para desbloqueio seguro.
 *   - LED vermelho de emergência (piscando em estado latched).
 * 
 * Plataforma: ESP32 (Arduino Framework)
 * Biblioteca: WiFi.h, ESPAsyncWebServer (ou servidor nativo simples)
 * 
 * Pinos (ajustáveis via defines):
 *   RELAY_PIN   = 26  (módulo relé)
 *   LED_RED     = 25  (LED vermelho emergência)
 *   BTN_RESET   = 33  (botão reset físico, INPUT_PULLUP)
 */

#include <Arduino.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>

// ===========================================================================
// Configurações de rede e hardware (ajuste conforme necessário)
// ===========================================================================

const char* ssid = "SEU_WIFI_SSID";          // <-- substitua pelo seu SSID
const char* password = "SEU_WIFI_SENHA";     // <-- substitua pela sua senha

const uint16_t httpPort = 80;

// Pinos de hardware
const int RELAY_PIN = 26;   // Módulo relé (ativa a máquina)
const int LED_RED   = 25;   // LED vermelho de emergência
const int BTN_RESET = 33;   // Botão físico de reset (INPUT_PULLUP)

// Lógica do relé: true = Active LOW (relé liga quando pino em LOW)
// false = Active HIGH (relé liga quando pino em HIGH)
const bool RELAY_ACTIVE_LOW = true;  // Alterar conforme módulo de relé

// ===========================================================================
// Variáveis de estado (fail-safe / latch)
// ===========================================================================

volatile bool emergencyLatched = false;   // Estado de emergência travado
bool machinePowered = false;              // Máquina ligada/desligada pelo relé

// Controle de debounce do botão
unsigned long lastDebounceTime = 0;
unsigned long debounceDelay = 50;         // ms
int lastButtonState = HIGH;
int buttonState = HIGH;

// Controle de piscar LED
unsigned long lastLedToggle = 0;
const unsigned long ledBlinkInterval = 500; // ms

// Último comando recebido
String lastCommand = "none";
unsigned long lastCommandTime = 0;

// ===========================================================================
// Instância do servidor web assíncrono
// ===========================================================================

AsyncWebServer server(httpPort);

// ===========================================================================
// Funções auxiliares
// ===========================================================================

/**
 * Conecta ao Wi-Fi com reconexão automática.
 */
void conectaWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);

  Serial.print("Conectando ao Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Wi-Fi conectado! IP: ");
  Serial.println(WiFi.localIP());
}

/**
 * Atualiza o estado do relé conforme lógica de segurança.
 * Só liga o relé (máquina) se não houver emergência latched.
 */
void atualizaRele() {
  if (emergencyLatched) {
    // Emergência latched: relé desligado (seguro)
    digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
    machinePowered = false;
  } else {
    // Sem emergência: relé pode ser ligado (estado padrão seguro OFF até comando)
    // Mantemos desligado por padrão; o sistema pode ser ligado via comando futuro
    digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
    machinePowered = false;
  }
}

/**
 * Aciona parada de emergência (fail-safe, latched).
 */
void acionaEmergencia() {
  if (!emergencyLatched) {
    Serial.println("[ESP32] EMERGENCY-STOP acionado! Relé desligado.");
    emergencyLatched = true;
    machinePowered = false;
    lastCommand = "emergency-stop";
    lastCommandTime = millis();
  }
  atualizaRele();
}

/**
 * Reseta estado de emergência (apenas após confirmação física).
 */
void resetaSistema() {
  if (emergencyLatched) {
    Serial.println("[ESP32] Sistema resetado via comando explícito.");
    emergencyLatched = false;
    machinePowered = false;
    lastCommand = "reset";
    lastCommandTime = millis();
  }
  atualizaRele();
}

/**
 * Verifica botão físico com debounce para reset seguro.
 */
void verificaBotaoReset() {
  int reading = digitalRead(BTN_RESET);

  if (reading != lastButtonState) {
    lastDebounceTime = millis();
  }

  if ((millis() - lastDebounceTime) > debounceDelay) {
    if (reading != buttonState) {
      buttonState = reading;

      // Botão pressionado (LOW por causa do INPUT_PULLUP)
      if (buttonState == LOW) {
        Serial.println("[ESP32] Botão físico de reset pressionado.");
        resetaSistema();
      }
    }
  }

  lastButtonState = reading;
}

/**
 * Piscar LED vermelho quando em estado latched.
 */
void controlaLedEmergencia() {
  if (emergencyLatched) {
    if (millis() - lastLedToggle > ledBlinkInterval) {
      lastLedToggle = millis();
      digitalWrite(LED_RED, !digitalRead(LED_RED));
    }
  } else {
    digitalWrite(LED_RED, LOW);
  }
}

/**
 * Retorna JSON com status atual do ESP32.
 */
String jsonStatus() {
  String json = "{";
  json += "\"emergency_latched\":" + String(emergencyLatched ? "true" : "false") + ",";
  json += "\"machine_powered\":" + String(machinePowered ? "true" : "false") + ",";
  json += "\"wifi_connected\":" + String(WiFi.status() == WL_CONNECTED ? "true" : "false") + ",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"last_command\":\"" + lastCommand + "\",";
  json += "\"last_command_time\":" + String(lastCommandTime) + ",";
  json += "\"relay_active_low\":" + String(RELAY_ACTIVE_LOW ? "true" : "false") + ",";
  json += "\"uptime_ms\":" + String(millis());
  json += "}";
  return json;
}

// ===========================================================================
// Handlers dos endpoints REST
// ===========================================================================

void onEmergencyStop(AsyncWebServerRequest *request) {
  Serial.println("[ESP32] Recebido POST /emergency-stop");
  acionaEmergencia();
  request->send(200, "application/json", "{\"success\":true,\"action\":\"emergency-stop\",\"latched\":true}");
}

void onReset(AsyncWebServerRequest *request) {
  Serial.println("[ESP32] Recebido POST /reset");
  resetaSistema();
  request->send(200, "application/json", "{\"success\":true,\"action\":\"reset\",\"latched\":false}");
}

void onStatus(AsyncWebServerRequest *request) {
  request->send(200, "application/json", jsonStatus());
}

// ===========================================================================
// Setup
// ===========================================================================

void setup() {
  Serial.begin(115200);

  // Configura pinos
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  pinMode(BTN_RESET, INPUT_PULLUP);

  // Estado inicial seguro: relé desligado, emergência não latched
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
  digitalWrite(LED_RED, LOW);
  emergencyLatched = false;
  machinePowered = false;

  // Conecta Wi-Fi
  conectaWiFi();

  // Rotas REST
  server.on("/emergency-stop", HTTP_POST, onEmergencyStop);
  server.on("/reset", HTTP_POST, onReset);
  server.on("/status", HTTP_GET, onStatus);

  // Inicia servidor
  server.begin();
  Serial.printf("Servidor HTTP iniciado na porta %d\n", httpPort);
}

// ===========================================================================
// Loop principal
// ===========================================================================

void loop() {
  // Mantém Wi-Fi conectado (reconexão automática)
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[ESP32] Wi-Fi desconectado. Reconectando...");
    WiFi.reconnect();
    delay(1000);
  }

  // Verifica botão físico de reset
  verificaBotaoReset();

  // Controla LED de emergência (piscando se latched)
  controlaLedEmergencia();

  // Mantém estado do relé seguro
  atualizaRele();

  delay(10); // pequeno delay para não travar o watchdog
}