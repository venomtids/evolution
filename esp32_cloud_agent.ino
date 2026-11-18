/*
 * esp32_cloud_agent.ino
 * ESP32 -> nuvem (Vercel + Neon) — E-Stop SEM portas abertas e SEM computador.
 *
 * O ESP32 faz contato HTTP(S) de SAÍDA a cada 2s em:
 *   POST {CLOUD_BASE}/device/heartbeat   (header X-Device-Token)
 *     -> recebe comandos pendentes ("emergency_stop" | "reset")
 *   POST {CLOUD_BASE}/device/ack         (confirma execução de cada comando)
 *
 * A cadeia de segurança física continua no hardware (relé latched, fail-safe,
 * botão físico de desbloqueio). A nuvem só CORTAR energia (fail-safe) ou
 * liberar sob comando explícito confirmado no site logável.
 *
 * Placa: ESP32 (Arduino Framework)
 * Bibliotecas (Library Manager):
 *   - ArduinoJson (Benoit Blanchon) v6 ou v7
 *   - OneWire (Paul Stoffregen)
 *   - DallasTemperature (Miles Burton)
 *   WiFi/HTTPClient/WiFiClientSecure já vêm no core ESP32.
 *
 * Pinos (iguais ao esp32_controller.ino):
 *   RELAY_PIN = 26 | LED_RED = 25 | BTN_RESET = 33 | TEMP_DS18B20 = 4
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <OneWire.h>
#include <DallasTemperature.h>

// ===== AJUSTE AQUI =========================================================
const char* WIFI_SSID     = "SEU_WIFI_SSID";
const char* WIFI_PASSWORD = "SEU_WIFI_SENHA";

// Mesmo valor do DEVICE_TOKEN configurado na Vercel
const char* DEVICE_ID     = "esp32-01";
const char* DEVICE_TOKEN  = "COLE_O_DEVICE_TOKEN_DA_VERCEL_AQUI";

const char* CLOUD_BASE    = "https://evolution-beta-six.vercel.app";
// ==========================================================================

const uint32_t HEARTBEAT_MS   = 2000;
const int RELAY_PIN = 26;
const int LED_RED   = 25;
const int BTN_RESET = 33;
const int TEMP_DQ   = 4;   // DATA do DS18B20 (pull-up 4,7k para 3,3V)
const bool RELAY_ACTIVE_LOW = true;
const char* FIRMWARE = "cloud-agent-1.0.0";

OneWire oneWire(TEMP_DQ);
DallasTemperature tempSensor(&oneWire);

volatile bool emergencyLatched = false;
bool machinePowered = false;
unsigned long lastHeartbeat = 0;
unsigned long lastLedToggle = 0;
const unsigned long ledBlinkInterval = 250;
unsigned long lastButtonEvent = 0;
int lastButtonState = HIGH;

// ---------- hardware ----------
void atualizaRele() {
  digitalWrite(RELAY_PIN, emergencyLatched ? (RELAY_ACTIVE_LOW ? HIGH : LOW)
                                           : (RELAY_ACTIVE_LOW ? LOW : HIGH));
  machinePowered = !emergencyLatched;
}

void acionaEmergencia() {
  if (!emergencyLatched) {
    Serial.println("[AGENT] EMERGENCY-STOP pela nuvem! Rele desligado (latch).");
    emergencyLatched = true;
    atualizaRele();
  }
}

void resetaSistema() {
  if (emergencyLatched) {
    Serial.println("[AGENT] Reset pela nuvem (ou botao fisico). Latch liberado.");
    emergencyLatched = false;
    atualizaRele();
  }
}

void verificaBotaoReset() {
  int r = digitalRead(BTN_RESET);
  if (r != lastButtonState) { lastButtonEvent = millis(); lastButtonState = r; }
  if (r == LOW && (millis() - lastButtonEvent) > 60 && lastButtonEvent != 0) {
    if (millis() - lastButtonEvent < 700) {  // debounce simples
      Serial.println("[AGENT] Botao fisico pressionado.");
      resetaSistema();
      lastButtonEvent = 0;
    }
  }
}

void controlaLed() {
  if (emergencyLatched) {
    if (millis() - lastLedToggle > ledBlinkInterval) {
      lastLedToggle = millis();
      digitalWrite(LED_RED, !digitalRead(LED_RED));
    }
  } else {
    digitalWrite(LED_RED, LOW);
  }
}

// ---------- nuvem ----------
bool postJson(const char* path, const char* body, String& response) {
  WiFiClientSecure client;
  client.setInsecure();  // AVISO: sem verificacao de certificado (acelera bootstrap).
                         // Para producao: grave o ISRG Root X1 e use setCACert().
  HTTPClient http;
  String url = String(CLOUD_BASE) + path;
  if (!http.begin(client, url)) { Serial.println("[AGENT] http.begin falhou"); return false; }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  int code = http.POST((uint8_t*)body, strlen(body));
  if (code > 0) response = http.getString();
  http.end();
  if (code != 200) {
    Serial.printf("[AGENT] %s -> HTTP %d\n", path, code);
    return false;
  }
  return true;
}

void sendHeartbeat() {
  tempSensor.requestTemperatures();
  float t = tempSensor.getTempCByIndex(0);  // -127 = sensor ausente

  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  if (t > -100.0f && t < 150.0f) doc["temperature"] = serialized(String(t, 2));
  doc["latched"] = emergencyLatched;
  doc["firmware"] = FIRMWARE;
  String body;
  serializeJson(doc, body);

  String response;
  if (!postJson("/device/heartbeat", body.c_str(), response)) return;

  JsonDocument rx;
  if (deserializeJson(rx, response)) { Serial.println("[AGENT] resposta invalida"); return; }
  if (!rx["ok"]) return;

  for (JsonObject cmd : rx["commands"].as<JsonArray>()) {
    int id = cmd["id"] | 0;
    String action = cmd["action"] | "";
    String result = "executado";
    if (action == "emergency_stop") {
      acionaEmergencia();
      result = "latch_acionado";
    } else if (action == "reset") {
      resetaSistema();
      result = "latch_liberado";
    } else {
      result = "ignorado:" + action;
      Serial.printf("[AGENT] comando desconhecido: %s\n", action.c_str());
    }
    // confirma de volta
    JsonDocument ack;
    ack["device_id"] = DEVICE_ID;
    ack["command_id"] = id;
    ack["result"] = result;
    String ackBody;
    serializeJson(ack, ackBody);
    String ackResp;
    postJson("/device/ack", ackBody.c_str(), ackResp);
  }
}

// ---------- setup/loop ----------
void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_RED, OUTPUT);
  pinMode(BTN_RESET, INPUT_PULLUP);
  digitalWrite(LED_RED, LOW);
  atualizaRele();  // estado seguro: latch livre no boot (igual ao sketch original)

  tempSensor.begin();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[AGENT] Conectando WiFi");
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[AGENT] WiFi %s | IP %s\n",
                WiFi.status() == WL_CONNECTED ? "OK" : "FALHOU",
                WiFi.localIP().toString().c_str());
}

void loop() {
  verificaBotaoReset();
  controlaLed();

  if (millis() - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = millis();
    if (WiFi.status() != WL_CONNECTED) {
      WiFi.reconnect();
      return;
    }
    sendHeartbeat();
  }
}
