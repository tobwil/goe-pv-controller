# ☀️ go-e PV Controller

Standalone **PV surplus charging controller** for [go-e Gemini](https://go-e.com/) wallboxes with Sungrow inverters. Direct Modbus-TCP + HTTP — no Home Assistant required.

![Dashboard](dashboard.png)

## Why?

Most PV surplus charging solutions depend on Home Assistant, which adds complexity, latency, and a single point of failure. This controller talks directly to your hardware:

```
Sungrow Inverter ←→ Modbus TCP ←→ Controller ←→ HTTP ←→ go-e Wallbox
```

No MQTT, no HA automations, no YAML configs to break on update.

## Features

- **Direct Modbus-TCP** to Sungrow inverters (single + dual WR setups)
- **Direct HTTP** to go-e Gemini API — reads car state, actual amps/watts, and sends commands
- **Automatic 1-phase / 3-phase switching** with hysteresis and cooldown
- **Decision matrix** engine: maps available surplus to the optimal amp/phase combination
- **Live web dashboard** on port 8088 with:
  - Real-time power values (PV, grid, battery, house, wallbox)
  - Decision matrix showing all 9 charging stages
  - 1-hour history chart (persists across restarts)
  - Scrollable decision log with error tracking
  - Toggleable logging
- **Persistent history** — chart data survives service restarts
- **PID-free** direct amp calculation: `pGrid / (230V × phases)` — no tuning, no windup
- **systemd user service** with auto-restart on failure

## Hardware

| Component | Model | Connection |
|-----------|-------|------------|
| Inverter WR1 | Sungrow SH8.0RT | Modbus TCP (192.168.178.151:502) |
| Inverter WR2 | Sungrow (secondary) | Modbus TCP (192.168.178.154:502) |
| Wallbox | go-e Gemini V4 | HTTP API (192.168.178.200) |
| Host | Raspberry Pi 4B | Runs controller + dashboard |

## Installation

```bash
# Clone
git clone https://github.com/tobwil/goe-pv-controller.git
cd goe-pv-controller

# Virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp config.yaml config.yaml  # edit IPs and thresholds
```

Edit `config.yaml`:

```yaml
goe:
  ip: "192.168.178.200"     # Your go-e IP

# Sungrow inverters: edit WR1/WR2 IPs in controller.py if needed

pid:
  output_limits: [6, 12]    # Min/max amps (go-e Gemini: 6-12A)

phase_switch:
  hysteresis_up: 4500       # 1→3 phase above this surplus
  hysteresis_down: 3200     # 3→1 phase below this surplus
  min_switch_interval: 300  # 5 min cooldown
```

## Usage

### Manual run

```bash
source venv/bin/activate
python web_ui.py
# Dashboard: http://<your-ip>:8088
```

### systemd service

```bash
mkdir -p ~/.config/systemd/user
cp pv-wallbox-controller.service ~/.config/systemd/user/
# Edit ExecStart path in the service file
systemctl --user daemon-reload
systemctl --user enable --now pv-wallbox-controller
```

## Decision Matrix

| pGrid Range | Phase | Amps | Charging Power |
|-------------|-------|------|----------------|
| 0 – 1,379 W | — | 0 A | 🛑 STOP |
| 1,380 – 1,839 W | 1ph | 6 A | ~1,380 W |
| 1,840 – 2,299 W | 1ph | 8 A | ~1,840 W |
| 2,300 – 2,759 W | 1ph | 10 A | ~2,300 W |
| 2,760 – 4,499 W | 1ph | 12 A | ~2,760 W |
| 4,500 – 5,519 W | 3ph | 6 A | ~4,140 W |
| 5,520 – 6,899 W | 3ph | 8 A | ~5,520 W |
| 6,900 – 8,279 W | 3ph | 10 A | ~6,900 W |
| ≥ 8,280 W | 3ph | 12 A | ~8,280 W |

Phase switching includes a **500 W deadband** and **5-minute cooldown** to prevent flapping from passing clouds.

## Dashboard API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/state` | GET | Full state JSON (sensors, decision, history, logs) |
| `/api/toggle_log` | POST | Toggle logging on/off |
| `/ws` | WebSocket | Live state push (auto-refresh) |

## pGrid Calculation

```
If battery charging:  pGrid = grid_feedin + wallbox_load
If battery discharging: pGrid = grid_feedin + wallbox_load - battery_discharge
If grid importing:     pGrid = 0  (no surplus available)
```

`pGrid` represents the **available surplus power** that can safely be sent to the car without pulling from the grid or battery.

## Files

```
├── controller.py          # Core logic: Modbus, go-e API, PID, phase switching
├── web_ui.py              # FastAPI dashboard + API endpoints
├── config.yaml            # Configuration (IPs, thresholds, PID params)
├── requirements.txt       # Python dependencies
├── pv-wallbox-controller.service  # systemd user unit
├── templates/
│   └── dashboard.html     # Live dashboard with chart.js
├── history.json           # Auto-generated: persists chart data across restarts
└── simulator.log          # Decision log (rotating, max 4 MB)
```

## License

MIT
