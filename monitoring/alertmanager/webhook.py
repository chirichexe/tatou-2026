import http.server
import json
import os
import sys
from datetime import datetime, timezone

LOG_FILE = os.environ.get("ALERT_LOG_PATH", "/alerts/alerts.log")


class AlertWebhookHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path in ("/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len)

        try:
            data = json.loads(post_body.decode("utf-8"))
            global_status = data.get("status", "unknown").upper()
            alerts = data.get("alerts", [])
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

            with open(LOG_FILE, "a", encoding="utf-8") as f:
                for alert in alerts:
                    status = alert.get("status", global_status).upper()
                    labels = alert.get("labels", {})
                    annotations = alert.get("annotations", {})

                    alert_name = labels.get("alertname", "UnknownAlert")
                    severity = labels.get("severity", "unknown").upper()
                    summary = annotations.get("summary", "No summary provided")
                    description = annotations.get("description", "No description provided")
                    target = labels.get("name") or labels.get("instance") or labels.get("compose_service") or "tatou"

                    log_line = (
                        f"[{now_str}] [{status}] [{severity}] "
                        f"Alert: {alert_name} | Target: {target} | "
                        f"Summary: {summary} | Description: {description}\n"
                    )
                    f.write(log_line)
                    sys.stdout.write(log_line)
                f.flush()
                sys.stdout.flush()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        except Exception as e:
            sys.stderr.write(f"Error processing webhook: {e}\n")
            sys.stderr.flush()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"error"}')

    def log_message(self, format, *args):
        # Suppress standard HTTP access log spam on stdout
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "9095"))
    print(f"Starting alert logger on port {port}, logging alerts to {LOG_FILE}", flush=True)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), AlertWebhookHandler)
    server.serve_forever()
