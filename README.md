# ☀️ go-e PV Controller

**Standalone PV-Überschuss-Laderegler** für go-e Gemini Wallboxen mit Sungrow-Wechselrichtern. Läuft unabhängig von Home Assistant — kann aber parallel dazu betrieben werden.

![Dashboard](dashboard.png)

## Warum standalone?

Es gibt exzellente Home-Assistant-Integrationen für Sungrow und go-e. Dieser Controller geht einen anderen Weg:

- **Direkte Hardware-Ansprache** — Modbus-TCP zum Wechselrichter, HTTP zur Wallbox
- **Kein MQTT, keine YAML-Automationen, keine Template-Sensoren**
- **Kürzere Steuerkette** → weniger Latenz, weniger Fehlerquellen
- **Läuft parallel zu HA** — die bestehende sungrow-bridge/HA-Integration bleibt unangetastet

Der Controller ist kein „besseres HA", sondern eine **dedizierte Lösung** für genau eine Aufgabe: PV-Überschuss präzise ins Auto schieben.

```
┌──────────────────┐     Modbus TCP      ┌──────────────────┐
│  Sungrow SH8.0RT │ ◄──────────────────► │                  │
│   (WR1 + WR2)    │                      │   go-e PV        │
└──────────────────┘                      │   Controller     │
                                          │   (Python)       │
┌──────────────────┐     HTTP API         │                  │
│  go-e Gemini V4   │ ◄──────────────────► │   Port 8088      │
│  (Wallbox)        │                      │   Dashboard      │
└──────────────────┘                      └──────────────────┘
                                                  │
                                          ┌───────┴───────┐
                                          │  history.json  │
                                          │  simulator.log │
                                          │  config.yaml   │
                                          └───────────────┘
```

## Features

### Regelung
- **PV-geführte Ampere-Wahl** — `pGrid / (230V × Phasen)` ergibt den optimalen Ladestrom
- **Automatische Phasenumschaltung** 1-phasig ↔ 3-phasig mit Hysterese
- **Sanfte Übergänge** — amp=0 → Phasenwechsel → neues amp (schützt die go-e)
- **5-Minuten-Sperre** zwischen Phasenwechseln gegen Wolken-Flackern
- **500-W-Deadband** — ignoriert kleine Schwankungen
- **Netzbezug-Schutz** — pGrid=0 sobald Strom aus dem Netz gezogen wird

### Dashboard (Port 8088)
- Live-Werte: PV, Netz, Batterie, Hausverbrauch, Wallbox, pGrid
- **Entscheidungsmatrix** — alle 9 Ladestufen auf einen Blick
- **1-Stunden-Chart** (übersteht Controller-Neustarts)
- Entscheidungs-Log mit Fehlertracking
- Logging per Button toggelbar
- Auto-Refresh alle 2 Sekunden via WebSocket

### Robustheit
- Modbus-Fallback: bei Timeout werden letzte gültige Werte gehalten
- Kein PID-Windup, keine nachschwingenden Integrale
- Rotating Log (max. 4 MB), schreibt nur bei Zustandsänderungen
- systemd-User-Service mit Auto-Restart

## Hardware

| Komponente | Typ | Verbindung |
|-----------|-----|------------|
| WR1 | Sungrow SH8.0RT | Modbus TCP `192.168.178.151:502` |
| WR2 | Sungrow (sekundär) | Modbus TCP `192.168.178.154:502` |
| Wallbox | go-e Gemini V4, FW 60.5 | HTTP API `192.168.178.200` |
| Host | Raspberry Pi 4B, Debian 13 | — |

> **Andere Hardware?** Der Controller nutzt Sungrow-Modbus-Register (13007, 13009, 13021, 5016). Für andere Wechselrichter müssen die Register-Adressen in `controller.py` angepasst werden. Die go-e-HTTP-Steuerung funktioniert mit allen Gemini-Modellen.

## Installation

```bash
git clone https://github.com/tobwil/goe-pv-controller.git
cd goe-pv-controller

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Konfiguration

`config.yaml` anpassen:

```yaml
goe:
  ip: "192.168.178.200"     # IP deiner go-e
  max_amps: 12              # Maximum (Hardware-Limit)
  min_amps: 6               # Minimum (unter 6A lädt kein Auto)
  min_power_w: 1380         # 6A × 230V (1-phasig Minimum)

phase_switch:
  hysteresis_up: 4500       # Ab hier: 1→3 Phasen
  hysteresis_down: 3200     # Darunter: 3→1 Phasen
  min_switch_interval: 300  # 5 Min Sperre

deadband:
  amps: 3                   # Nur ändern wenn Δ ≥ 3A
  watts: 500                # Nur ändern wenn Δ ≥ 500W

simulation:
  enabled: false            # false = LIVE, true = nur loggen
  log_file: "/pfad/zum/simulator.log"

history:
  file: "/pfad/zum/history.json"

web:
  host: "0.0.0.0"
  port: 8088
```

### systemd Service

```bash
mkdir -p ~/.config/systemd/user
cp pv-wallbox-controller.service ~/.config/systemd/user/

# Pfade im Service-File anpassen:
# WorkingDirectory=/pfad/zum/goe-pv-controller
# ExecStart=/pfad/zum/goe-pv-controller/venv/bin/python .../web_ui.py

systemctl --user daemon-reload
systemctl --user enable --now pv-wallbox-controller

# Status prüfen
systemctl --user status pv-wallbox-controller
```

## Dashboard

Erreichbar unter `http://<host>:8088`

### Oben: Live-Werte
Fünf Karten zeigen PV-Produktion, Netzeinspeisung/-bezug, Batterie-Ladung/Entladung, Hausverbrauch und Wallbox-Last. Der **pGrid**-Wert ist der verfügbare Überschuss — das ist die Steuergröße.

### Mitte: Entscheidungsmatrix

| pGrid | Phase | Ampere | Ladeleistung |
|-------|-------|--------|-------------|
| 0 – 1.379 W | — | 0 A | 🛑 STOP |
| 1.380 – 1.839 W | 1ph | 6 A | ~1.380 W |
| 1.840 – 2.299 W | 1ph | 8 A | ~1.840 W |
| 2.300 – 2.759 W | 1ph | 10 A | ~2.300 W |
| 2.760 – 4.499 W | 1ph | 12 A | ~2.760 W |
| 4.500 – 5.519 W | 3ph | 6 A | ~4.140 W |
| 5.520 – 6.899 W | 3ph | 8 A | ~5.520 W |
| 6.900 – 8.279 W | 3ph | 10 A | ~6.900 W |
| ≥ 8.280 W | 3ph | 12 A | ~8.280 W |

Die Phasenumschaltung nutzt eine Hysterese: Hochschalten bei 4.500 W, Runterschalten bei 3.200 W. Dazwischen bleibt die aktuelle Phase — kein Hin-und-her bei Wolken.

### Unten: Chart & Log
Der 1-Stunden-Verlauf zeigt pGrid, PV, target amps und Ist-Amps. Darunter das Entscheidungs-Log mit Timeouts, Connection-Errors und Schaltentscheidungen.

### API

| Endpoint | Methode | Beschreibung |
|----------|---------|-------------|
| `/` | GET | Dashboard HTML |
| `/api/state` | GET | JSON: Sensoren, Entscheidung, History, Logs |
| `/api/toggle_log` | POST | Logging an/aus |
| `/ws` | WebSocket | Live-Push (alle 2s) |

## pGrid — die zentrale Steuergröße

`pGrid` ist der **für die Wallbox verfügbare Überschuss** in Watt. Die Formel berücksichtigt Batterie-Ladung und Netzbezug:

```
Wenn Batterie lädt:      pGrid = Netzeinspeisung + Wallbox-Last
Wenn Batterie entlädt:   pGrid = Netzeinspeisung + Wallbox-Last − Batterie-Entladung
Wenn Netzbezug (< −50W): pGrid = 0  (kein Überschuss)
```

Die Wallbox wird so zu 100% aus PV-Überschuss gespeist — kein Netzbezug, kein Batterie-Leersaugen.

## Phasenwechsel — so funktioniert's

Ein Phasenwechsel unter Last kann die go-e beschädigen. Der Controller macht das sicher:

1. **amp=0** → Wallbox stoppt Ladung
2. **0,5s Pause** → go-e verarbeitet Stop
3. **psm=X** → Phasenmodus setzen (1=1ph, 2=3ph)
4. **amp=Y** → neue Ampere setzen

Der ganze Vorgang dauert <1 Sekunde und passiert nur alle ≥5 Minuten.

## Parallel zu Home Assistant

Der Controller läuft **unabhängig** von HA, stört es aber nicht:

- **sungrow-bridge** bleibt unverändert — HA bekommt weiterhin seine PV-Daten
- **go-e-Integration** in HA läuft weiter — du siehst Ladezustand etc. wie gewohnt
- Der Controller liest **nur** (Modbus → WR) und schreibt **nur** zur go-e (amp, psm)
- Kein MQTT-Topic-Overlap, keine doppelten Sensoren

Du kannst den Controller also betreiben ohne HA auch nur anzufassen.

## Troubleshooting

| Symptom | Ursache | Lösung |
|---------|---------|--------|
| Controller stoppt sofort (pGrid=0) | Kein PV-Überschuss oder Netzbezug | Normal bei Bewölkung/Nacht |
| „Connection reset by peer" im Log | Modbus-Verbindung kurz abgerissen | Harmlos — Controller nutzt Fallback-Werte |
| TimeoutError | WR oder go-e temporär nicht erreichbar | Controller retried automatisch nächsten Zyklus |
| Dashboard zeigt leeren Chart | Controller neu gestartet, history.json fehlt | Nach 1h wieder voll — history wird alle 30s persistiert |
| go-e lädt nicht trotz Überschuss | `alw=False` in go-e API | Ladeerlaubnis in go-e-App aktivieren |
| Phasenwechsel flackert | Hysterese zu knapp | `hysteresis_up`/`down` weiter auseinander setzen |

## Dateien

```
├── controller.py          # Kernlogik: Modbus, go-e API, Regelung, Phasenwechsel
├── web_ui.py              # FastAPI: Dashboard + API-Endpunkte
├── config.yaml            # Konfiguration (IPs, Schwellwerte)
├── requirements.txt       # Python-Abhängigkeiten
├── pv-wallbox-controller.service  # systemd-User-Unit
├── templates/
│   └── dashboard.html     # Live-Dashboard mit Chart.js
├── dashboard.png          # Screenshot für README
├── history.json           # Auto-generiert: Chart-Daten (übersteht Neustarts)
└── simulator.log          # Entscheidungs-Log (rotierend, max. 4 MB)
```

## Roadmap

- [ ] MQTT-Status-Push (optional, für HA-Einbindung falls gewünscht)
- [ ] Config-UI im Dashboard
- [ ] Docker-Container
- [ ] Unterstützung für weitere Wechselrichter (GoodWe, Fronius, Solax)

## License

MIT
