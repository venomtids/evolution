"""
mock_esp32.py
Servidor HTTP mock para simular o ESP32 do projeto EVO-VISION.

Implementa:
  POST /emergency-stop : Retorna { "success": true, "action": "emergency-stop", "latched": true }
  POST /reset          : Retorna { "success": true, "action": "reset", "latched": false }
  GET  /status         : Retorna JSON com estado atual (inclui temperature simulada)

Uso:
    python mock_esp32.py                    # escuta 0.0.0.0:80 (porta ESP32 real)
    python mock_esp32.py --port 8080        # porta alternativa (ex: 8080)
"""

import argparse
import json
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockESP32Handler(BaseHTTPRequestHandler):
    last_command = "none"
    last_command_time = 0.0
    emergency_latched = False
    temperature = 38.5
    start_time = time.time()

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Log simples com timestamp
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[MOCK ESP32 {ts}] {format % args}")

    def do_POST(self) -> None:
        if self.path == "/emergency-stop":
            MockESP32Handler.last_command = "emergency-stop"
            MockESP32Handler.last_command_time = time.time()
            MockESP32Handler.emergency_latched = True
            self._send_json({"success": True, "action": "emergency-stop", "latched": True})
            return
        if self.path == "/reset":
            MockESP32Handler.last_command = "reset"
            MockESP32Handler.last_command_time = time.time()
            MockESP32Handler.emergency_latched = False
            self._send_json({"success": True, "action": "reset", "latched": False})
            return
        self._send_json({"success": False, "error": "unknown endpoint"}, status=404)

    def do_GET(self) -> None:
        if self.path == "/status":
            # Temperatura simulada: cai lentamente se não latched
            if not MockESP32Handler.emergency_latched:
                MockESP32Handler.temperature = max(38.0, MockESP32Handler.temperature - 0.1)
            else:
                MockESP32Handler.temperature = min(90.0, MockESP32Handler.temperature + 0.5)

            self._send_json({
                "emergency_latched": MockESP32Handler.emergency_latched,
                "machine_powered": not MockESP32Handler.emergency_latched,
                "wifi_connected": True,
                "ip": "127.0.0.1",
                "last_command": MockESP32Handler.last_command,
                "last_command_time": MockESP32Handler.last_command_time,
                "relay_active_low": True,
                "uptime_ms": int((time.time() - self.start_time) * 1000),
                "temperature": round(MockESP32Handler.temperature, 2),
            })
            return
        self._send_json({"success": False, "error": "unknown endpoint"}, status=404)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mock ESP32 for EVO-VISION testing")
    parser.add_argument("--port", type=int, default=80)
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), MockESP32Handler)
    print(f"[MOCK ESP32] Listening on 0.0.0.0:{args.port}")
    print(f"[MOCK ESP32] Endpoints: GET /status, POST /emergency-stop, POST /reset")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MOCK ESP32] Stopped.")
