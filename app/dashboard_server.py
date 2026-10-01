import os
import json
import sqlite3
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta

# Decoupled Standalone Dashboard Server for Botesh Scraper
# Compute paths relative to project root (one level above this file)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE_PATH = os.path.join(_BASE_DIR, "logs", "app.log")
CACHE_FILE_PATH = os.path.join(_BASE_DIR, "session.json")
DB_FILE_PATH = os.path.join(_BASE_DIR, "data", "jobs.db")
if not os.path.exists(DB_FILE_PATH):
    DB_FILE_PATH = os.path.join(_BASE_DIR, "jobs.db")

def get_recent_errors_count():
    if not os.path.exists(LOG_FILE_PATH):
        return 0
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-300:] # check last 300 lines
            one_hour_ago = datetime.now() - timedelta(hours=1)
            error_count = 0
            for line in lines:
                # Actual log format: "2026-07-27 02:09:43 - upwork_bot - INFO - ..."
                m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if m:
                    try:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                        if ts >= one_hour_ago and ("ERROR" in line or "WARNING" in line or "❌" in line or "⚠️" in line):
                            error_count += 1
                    except Exception:
                        pass
            return error_count
    except Exception:
        return 0

def get_bot_uptime_hours():
    if not os.path.exists(LOG_FILE_PATH):
        return 0.0
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            launch_time = None
            for line in reversed(lines):
                # Match the actual startup message logged by main.py
                if "Initializing Upwork Monitor Bot" in line:
                    # Actual log format: "2026-07-27 02:09:43 - upwork_bot - INFO - ..."
                    m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                    if m:
                        try:
                            launch_time = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                            break
                        except Exception:
                            pass
            if launch_time:
                elapsed = datetime.now() - launch_time
                return round(elapsed.total_seconds() / 3600, 2)
    except Exception:
        pass
    return 0.0

def get_bot_memory_usage_mb():
    if not os.path.exists(LOG_FILE_PATH):
        return 0.0
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-200:]
            for line in reversed(lines):
                # Actual log: "Process RSS memory: 73.41 MB" (lowercase 'm', no 'Usage')
                m = re.search(r"Process RSS memory:\s*(\d+\.?\d*)\s*MB", line)
                if m:
                    return float(m.group(1))
    except Exception:
        pass
    return 0.0

def get_token_status():
    if not os.path.exists(CACHE_FILE_PATH):
        return "Expired / Uninitialized", "N/A"
    try:
        with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            ts_raw = data.get("timestamp")
            if not ts_raw:
                return "Expired / Uninitialized", "N/A"

            # session.json stores timestamp as a Unix float (time.time()), not an ISO string
            ts = datetime.fromtimestamp(float(ts_raw))
            elapsed = datetime.now() - ts
            lifetime = timedelta(hours=11)

            if elapsed >= lifetime:
                return "Expired", ts.strftime("%Y-%m-%d %H:%M:%S")

            remaining = lifetime - elapsed
            hours, remainder = divmod(remaining.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            countdown = f"{hours}h {minutes}m remaining"
            return f"Active ({countdown})", ts.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "Error Reading Cache", "N/A"

def get_db_stats():
    targets = []
    total_seen_jobs = 0
    if os.path.exists(DB_FILE_PATH):
        try:
            # Connect to sqlite database directly
            conn = sqlite3.connect(DB_FILE_PATH)
            # Fetch active targets
            cursor = conn.execute("SELECT channel_id, label, user_query FROM tracked_targets")
            for row in cursor.fetchall():
                targets.append({"channel_id": row[0], "label": row[1], "userQuery": row[2]})
            # Fetch total seen jobs count
            row = conn.execute("SELECT COUNT(*) FROM seen_jobs").fetchone()
            if row:
                total_seen_jobs = row[0]
            conn.close()
        except Exception:
            pass
    return targets, total_seen_jobs

def read_last_logs(limit=35):
    if not os.path.exists(LOG_FILE_PATH):
        return ["No log file found yet."]
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return [line.strip() for line in lines[-limit:]]
    except Exception as e:
        return [f"Error reading logs: {e}"]

class DashboardHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default terminal logs of HTTP server
        pass

    def do_GET(self):
        # API Status Endpoint
        if self.path in ['/api/status', '/status/api']:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            targets, total_seen_jobs = get_db_stats()
            token_state, token_time = get_token_status()
            
            status_data = {
                'uptime_hours': get_bot_uptime_hours(),
                'memory_usage_mb': get_bot_memory_usage_mb(),
                'active_targets_count': len(targets),
                'total_seen_jobs': total_seen_jobs,
                'errors_last_hour': get_recent_errors_count(),
                'token_status': token_state,
                'last_token_refresh': token_time,
                'targets': targets,
                'logs': read_last_logs()
            }
            self.wfile.write(json.dumps(status_data).encode('utf-8'))
            return

        # HTML Front Page
        elif self.path in ['/', '/status', '/dashboard']:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            
            html_content = self.get_dashboard_html()
            self.wfile.write(html_content.encode('utf-8'))
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def get_dashboard_html(self):
        return r"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Botesh Scraper Diagnostics Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-gradient: linear-gradient(135deg, #0e1210 0%, #151b17 100%);
            --panel-bg: rgba(28, 38, 32, 0.6);
            --border-color: rgba(43, 168, 116, 0.15);
            --border-glow: rgba(43, 168, 116, 0.3);
            --jade: #2ba874;
            --jade-bright: #3ee29f;
            --olive: #7c936e;
            --text-primary: #e8ebe9;
            --text-secondary: #9bad9e;
            --terminal-bg: rgba(10, 14, 11, 0.95);
            --danger: #cf5b5b;
            --card-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background: var(--bg-gradient);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 2rem 1.5rem;
            display: flex;
            justify-content: center;
            align-items: flex-start;
        }

        .container {
            width: 100%;
            max-width: 1200px;
            display: flex;
            flex-direction: column;
            gap: 2rem;
        }

        /* Header Style */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }

        header h1 {
            font-size: 2.2rem;
            font-weight: 700;
            letter-spacing: -0.5px;
            background: linear-gradient(to right, var(--text-primary), var(--olive));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            display: flex;
            align-items: center;
            gap: 0.8rem;
        }

        .pulse-indicator {
            width: 12px;
            height: 12px;
            background-color: var(--jade);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 0 0 rgba(43, 168, 116, 0.7);
            animation: pulse 1.8s infinite;
        }

        @keyframes pulse {
            0% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(43, 168, 116, 0.7);
            }
            70% {
                transform: scale(1);
                box-shadow: 0 0 0 8px rgba(43, 168, 116, 0);
            }
            100% {
                transform: scale(0.95);
                box-shadow: 0 0 0 0 rgba(43, 168, 116, 0);
            }
        }

        header .uptime-badge {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            padding: 0.5rem 1rem;
            border-radius: 50px;
            font-size: 0.9rem;
            font-weight: 500;
            color: var(--text-secondary);
        }

        /* Metrics Cards Grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 1.5rem;
        }

        .card {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(12px);
            box-shadow: var(--card-shadow);
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .card:hover {
            border-color: var(--border-glow);
            transform: translateY(-4px);
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--jade);
            opacity: 0.7;
        }

        .card.olive-left::before {
            background: var(--olive);
        }

        .card.danger-left::before {
            background: var(--danger);
        }

        .card-label {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1.2px;
            color: var(--text-secondary);
            font-weight: 600;
            margin-bottom: 0.5rem;
        }

        .card-value {
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 0.3rem;
        }

        .card-subtext {
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        /* Main Content Layout */
        .content-layout {
            display: grid;
            grid-template-columns: 1fr;
            gap: 2rem;
        }

        @media (min-width: 900px) {
            .content-layout {
                grid-template-columns: 1.2fr 1.8fr;
            }
        }

        /* Tables & Lists */
        .panel {
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            backdrop-filter: blur(12px);
            box-shadow: var(--card-shadow);
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            gap: 1.2rem;
        }

        .panel h2 {
            font-size: 1.3rem;
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 0.8rem;
            color: var(--text-primary);
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }

        .table-wrapper {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-secondary);
            padding: 0.8rem 0.5rem;
            border-bottom: 1px solid var(--border-color);
        }

        td {
            font-size: 0.95rem;
            padding: 0.9rem 0.5rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            color: var(--text-primary);
        }

        .badge {
            background: rgba(43, 168, 116, 0.15);
            color: var(--jade-bright);
            border: 1px solid rgba(43, 168, 116, 0.3);
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            font-size: 0.8rem;
            font-weight: 600;
        }

        /* Terminal Logs Viewer */
        .terminal-panel {
            display: flex;
            flex-direction: column;
            height: 520px;
        }

        .terminal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #0d120f;
            border: 1px solid var(--border-color);
            border-bottom: none;
            border-radius: 12px 12px 0 0;
            padding: 0.75rem 1.2rem;
        }

        .terminal-controls {
            display: flex;
            gap: 6px;
        }

        .control-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #ff5f56;
        }
        .control-dot.yellow { background: #ffbd2e; }
        .control-dot.green { background: #27c93f; }

        .terminal-title {
            font-family: monospace;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        .terminal-body {
            flex-grow: 1;
            background: var(--terminal-bg);
            border: 1px solid var(--border-color);
            border-radius: 0 0 12px 12px;
            padding: 1.2rem;
            font-family: 'Consolas', 'Fira Code', monospace;
            font-size: 0.88rem;
            line-height: 1.5;
            overflow-y: auto;
            color: #d1dcd4;
            box-shadow: inset 0 8px 24px rgba(0,0,0,0.8);
        }

        /* Logging styles inside terminal */
        .log-line {
            margin-bottom: 0.4rem;
            white-space: pre-wrap;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
            padding-bottom: 0.2rem;
        }
        
        .log-time { color: var(--olive); margin-right: 0.5rem; }
        .log-success { color: var(--jade-bright); }
        .log-info { color: #87ceeb; }
        .log-warning { color: #f4db5c; }
        .log-danger { color: var(--danger); }
        .log-system { color: #9c9c9c; }

    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <h1><span class="pulse-indicator"></span> Botesh Diagnostics Panel</h1>
            <div class="uptime-badge" id="uptime-display">Uptime: 0.00 hours</div>
        </header>

        <!-- Metric Grid -->
        <div class="metrics-grid">
            <!-- Active Targets -->
            <div class="card">
                <div class="card-label">Active Targets</div>
                <div class="card-value" id="active-targets-val">0</div>
                <div class="card-subtext">Queries running concurrently</div>
            </div>
            
            <!-- Seen Jobs -->
            <div class="card olive-left">
                <div class="card-label">Jobs Scraped</div>
                <div class="card-value" id="seen-jobs-val">0</div>
                <div class="card-subtext">Total unique jobs in database</div>
            </div>

            <!-- Memory usage -->
            <div class="card">
                <div class="card-label">Bot RAM Usage</div>
                <div class="card-value" id="ram-val">0.00 MB</div>
                <div class="card-subtext">Process memory allocation</div>
            </div>

            <!-- Token Status -->
            <div class="card olive-left" id="token-card">
                <div class="card-label">Visitor Token Status</div>
                <div class="card-value" id="token-status-val" style="font-size: 1.25rem; padding-top: 0.5rem; margin-bottom: 0.5rem;">Checking...</div>
                <div class="card-subtext" id="token-refresh-val">Last refreshed: N/A</div>
            </div>
        </div>

        <!-- Main Layout Split -->
        <div class="content-layout">
            <!-- Left Panel: Target Queries -->
            <div class="panel">
                <h2>📊 Tracked Targets</h2>
                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>Label</th>
                                <th>Query</th>
                            </tr>
                        </thead>
                        <tbody id="targets-tbody">
                            <tr>
                                <td colspan="2" style="text-align: center; color: var(--text-secondary);">No targets tracked.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Right Panel: Live Logs -->
            <div class="terminal-panel">
                <div class="terminal-header">
                    <div class="terminal-controls">
                        <div class="control-dot"></div>
                        <div class="control-dot yellow"></div>
                        <div class="control-dot.green green"></div>
                    </div>
                    <div class="terminal-title">bot_activity.log - Live Streaming</div>
                    <div style="width: 40px;"></div>
                </div>
                <div class="terminal-body" id="terminal-screen">
                    <div class="log-line"><span class="log-system">Initialising terminal stream handler...</span></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Live Update Script -->
    <script>
        const API_URL = '/api/status';

        function colorizeLogLine(line) {
            const timestampMatch = line.match(/^(\[[0-9\-:\s]+\])\s?(.*)$/);
            
            let timeStr = "";
            let restOfLine = line;

            if (timestampMatch) {
                timeStr = `<span class="log-time">${timestampMatch[1]}</span>`;
                restOfLine = timestampMatch[2];
            }

            let lineClass = "";
            if (restOfLine.includes("✅") || restOfLine.includes("Posting") || restOfLine.includes("kamyab") || restOfLine.includes("extracted") || restOfLine.includes("Bypassed")) {
                lineClass = "log-success";
            } else if (restOfLine.includes("❌") || restOfLine.includes("Error") || restOfLine.includes("failed")) {
                lineClass = "log-danger";
            } else if (restOfLine.includes("⚠️") || restOfLine.includes("Warning") || restOfLine.includes("Retrying") || restOfLine.includes("Proactive") || restOfLine.includes("Skipping")) {
                lineClass = "log-warning";
            } else if (restOfLine.includes("📡") || restOfLine.includes("Loop Triggered") || restOfLine.includes("Checking") || restOfLine.includes("Triggered")) {
                lineClass = "log-info";
            } else {
                lineClass = "log-system";
            }

            return `<div class="log-line">${timeStr}<span class="${lineClass}">${restOfLine}</span></div>`;
        }

        async function updateDashboard() {
            try {
                const response = await fetch(API_URL);
                if (!response.ok) throw new Error("API Connection Failed");
                const data = await response.json();

                // 1. Update Uptime Badge
                document.getElementById('uptime-display').textContent = `Uptime: ${data.uptime_hours.toFixed(2)} hours`;

                // 2. Update Metric Cards
                document.getElementById('active-targets-val').textContent = data.active_targets_count;
                document.getElementById('seen-jobs-val').textContent = data.total_seen_jobs;
                document.getElementById('ram-val').textContent = `${data.memory_usage_mb.toFixed(2)} MB`;
                
                // Token status
                const tokenVal = document.getElementById('token-status-val');
                tokenVal.textContent = data.token_status;
                if (data.token_status.includes("Active")) {
                    tokenVal.style.color = 'var(--jade-bright)';
                } else {
                    tokenVal.style.color = 'var(--danger)';
                }
                document.getElementById('token-refresh-val').textContent = `Last Refreshed: ${data.last_token_refresh}`;

                // 3. Update Targets Table
                const tbody = document.getElementById('targets-tbody');
                if (data.targets && data.targets.length > 0) {
                    tbody.innerHTML = data.targets.map(target => `
                        <tr>
                            <td><span class="badge">${target.label}</span></td>
                            <td><code>${target.userQuery}</code></td>
                        </tr>
                    `).join('');
                } else {
                    tbody.innerHTML = `<tr><td colspan="2" style="text-align: center; color: var(--text-secondary);">No targets tracked.</td></tr>`;
                }

                // 4. Update Terminal Logs
                const terminal = document.getElementById('terminal-screen');
                const wasScrolledToBottom = terminal.scrollHeight - terminal.clientHeight <= terminal.scrollTop + 40;

                if (data.logs && data.logs.length > 0) {
                    terminal.innerHTML = data.logs.map(line => colorizeLogLine(line)).join('');
                }

                // Auto Scroll to bottom if user was already at bottom
                if (wasScrolledToBottom) {
                    terminal.scrollTop = terminal.scrollHeight;
                }

            } catch (error) {
                console.error("Dashboard Auto-Update Error:", error);
            }
        }

        // Run updates
        updateDashboard();
        setInterval(updateDashboard, 5000); // Refresh every 5 seconds
    </script>
</body>
</html>
"""

def run_standalone_server(port=8080):
    for p in [port, 8081, 8000, 5000, 5001]:
        try:
            server = HTTPServer(('0.0.0.0', p), DashboardHTTPHandler)
            print("==================================================")
            print(f"🟢 [Monitoring] Premium Jade & Olive Dashboard active!")
            print(f"🔗 URL: http://localhost:{p}/")
            print("==================================================")
            server.serve_forever()
            break
        except OSError:
            continue
        except KeyboardInterrupt:
            print("\n👋 Dashboard Server shut down. Goodbye!")
            break
        except Exception as e:
            print(f"❌ [Dashboard Error]: {e}")
            break

if __name__ == "__main__":
    run_standalone_server()
