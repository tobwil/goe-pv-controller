#!/usr/bin/env python3
"""Web-UI for PV Wallbox Controller."""
import asyncio, json
from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
import uvicorn

from controller import loop, st, CFG, LOG_PATH

app = FastAPI(title="PV Wallbox Controller")
TDIR = Path(__file__).parent / "templates"

@app.get("/", response_class=HTMLResponse)
async def dash():
    return open(TDIR / "dashboard.html").read()

@app.get("/api/state")
async def state():
    s, d = st.s, st.d
    sd, dd = {}, {}
    if s:
        sd = {"pv_wr1":s.pv_wr1,"pv_wr2":s.pv_wr2,"pv_gesamt":s.pv,"netz":s.netz,
              "batt":s.batt,"haus_wr1":s.haus_wr1,"haus_netto":s.haus_netto,
              "wb_aktuell":s.wb_leistung,"wb_amps":s.wb_amps,"wb_car":s.wb_car,
              "pgrid":s.pgrid}
    if d:
        dd = {"action":d.action,"reason":d.reason,"target_amps":d.target_a,
              "target_phases":d.target_ph,"car_status":d.car,
              "pid_output":d.pid,"pv_gesamt":d.pv,"pgrid":d.pgrid}
    logs = []
    try:
        logs = [l.strip() for l in open(LOG_PATH).readlines()[-20:]]
    except: pass
    # Downsample history for chart (max 300 points = ~5 min at 1s)
    hist = st.history[-300:] if st.history else []
    # Thin to max 120 points for faster rendering
    if len(hist) > 120:
        step = len(hist) // 120
        hist = hist[::step]
    return {"simulation":st.sim,"sensors":sd,"decision":dd,
            "cycles":st.cyc,"errors":st.err,"logs":logs,"history":hist,
            "log_enabled":st.log_enabled}

@app.post("/api/toggle")
async def toggle():
    st.sim = not st.sim
    return {"simulation": st.sim}

@app.post("/api/toggle_log")
async def toggle_log():
    st.log_enabled = not st.log_enabled
    return {"log_enabled": st.log_enabled}

@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    try:
        while st.run:
            s,d=st.s,st.d
            await ws.send_json({
                "pv":s.pv if s else 0,"netz":s.netz if s else 0,"batt":s.batt if s else 0,
                "wb":s.wb_leistung if s else 0,"pgrid":s.pgrid if s else 0,
                "action":d.action if d else"WARTEN","target_amps":d.target_a if d else 0,
                "target_phases":d.target_ph if d else 3,"simulation":st.sim,"cycles":st.cyc
            })
            await asyncio.sleep(2)
    except: pass

async def main():
    loop_task = asyncio.create_task(loop())
    cfg = CFG["web"]
    config = uvicorn.Config(app,host=cfg["host"],port=cfg["port"],log_level="warning")
    server = uvicorn.Server(config)
    await asyncio.gather(server.serve(),loop_task)

if __name__=="__main__":
    asyncio.run(main())
