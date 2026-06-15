#!/usr/bin/env python3
"""Go-e PV-Uberschuss-Controller (standalone, Modbus direkt)."""
import asyncio, json, logging, struct, socket, time
from dataclasses import dataclass, field
from pathlib import Path
import aiohttp, yaml
from simple_pid import PID

from logging.handlers import RotatingFileHandler

CFG = yaml.safe_load(open(Path(__file__).parent / "config.yaml"))
GOE_IP = CFG["goe"]["ip"]
SIM_MODE = CFG["simulation"]["enabled"]
LOG_PATH = Path(CFG["simulation"]["log_file"])

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gctl")
fh = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
log.addHandler(fh)

# ─── Modbus Direktzugriff ─────────────────────────────────────────────────────
WR1 = ("192.168.178.151", 502, 1)
WR2 = ("192.168.178.154", 502, 1)
MODBUS_REGS = {5016:("pv_wr1",4), 13007:("haus_wr1",4), 13009:("netz",4), 13021:("batt",4)}

def _read_modbus_regs(ip, port, uid, regs):
    """Batch-Read Modbus Register (count=2 = 32-bit). regs = [(reg_num, func_code), ...]"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    results = {}
    try:
        s.connect((ip, port))
        for reg, fc in regs:
            addr = reg - 1
            req = struct.pack(">HHHBBHH", 0, 0, 6, uid, fc, addr, 2)
            s.sendall(req)
            mbap = s.recv(7)
            if len(mbap) < 7: continue
            pdu_len = (mbap[4] << 8) | mbap[5]
            pdu = s.recv(pdu_len - 1)
            if len(pdu) < 2 or pdu[0] != fc: continue
            bc = pdu[1]
            data = pdu[2:2+bc]
            vals = [int.from_bytes(data[i:i+2], "big") for i in range(0, len(data), 2)]
            results[reg] = vals
    except Exception:
        pass
    finally:
        s.close()
    return results

async def read_modbus():
    """Liest PV/Netz/Batt direkt via Modbus. Returns S oder None bei Fehler."""
    s = S()
    try:
        regs1 = [(reg, fc) for reg, (_, fc) in MODBUS_REGS.items()]
        r1 = await asyncio.to_thread(_read_modbus_regs, *WR1, regs1)
        for reg, (field, _) in MODBUS_REGS.items():
            if reg in r1 and r1[reg] and len(r1[reg]) >= 2:
                v = r1[reg][1]  # low word of 32-bit
                if reg in (13007, 13009, 13021) and v >= 0x8000:
                    v -= 0x10000
                setattr(s, field, v)
        r2 = await asyncio.to_thread(_read_modbus_regs, *WR2, [(5016, 4)])
        if 5016 in r2 and r2[5016] and len(r2[5016]) >= 2:
            s.pv_wr2 = r2[5016][1]
        return s
    except Exception as e:
        log.warning(f"Modbus: {e}")
        return None

# Letzter gültiger Modbus-State als Fallback
_last_mb = None

@dataclass
class S:
    pv_wr1:float=0;pv_wr2:float=0;netz:float=0;batt:float=0
    haus_wr1:float=0;wb_leistung:float=0;wb_amps:float=0;wb_car:int=0
    ts:float=field(default_factory=time.time)
    @property
    def pv(self):return self.pv_wr1+self.pv_wr2
    @property
    def bat_ch(self):return self.haus_wr1<0
    @property
    def haus_netto(self):
        """Hausverbrauch exkl. Wallbox und Batterie"""
        batt_signed = -self.batt if self.bat_ch else self.batt
        return max(self.pv + batt_signed - self.wb_leistung - self.netz, 0)
    @property
    def pgrid(self):
        # If clearly importing from grid, there can be no surplus
        if self.netz < -50:
            return 0
        raw = 0
        if self.bat_ch:
            raw = max(self.netz+self.wb_leistung, 0)
        else:
            raw = max(self.netz+self.wb_leistung-self.batt, 0)
        return raw if raw >= 200 else 0

@dataclass
class D:
    ts:float=field(default_factory=time.time);pv:float=0;netz:float=0
    batt:float=0;wb:float=0;pgrid:float=0;target_a:int=0;target_ph:int=3
    pid:float=0;action:str="WARTEN";reason:str="";car:int=0


async def read_goe_direct(sess):
    """Read wallbox state directly from go-e API (bypasses HA REST sensors)."""
    try:
        async with sess.get(f"http://{GOE_IP}/api/status?filter=car,amp,nrg,alw", timeout=3) as r:
            if r.status==200:
                data=await r.json()
                nrg=data.get("nrg",[])
                return {
                    "car": data.get("car",0),
                    "amp": data.get("amp",0),
                    "alw": data.get("alw",False),
                    "watt": nrg[11] if len(nrg)>11 else 0,
                    "voltage": nrg[0] if len(nrg)>0 else 230,
                }
    except: pass
    return None

class Ctrl:
    def __init__(self):
        p=CFG["pid"]
        self.pid=PID(p["kp"],p["ki"],p["kd"],setpoint=p["setpoint"],
                      sample_time=p["sample_time"],output_limits=(-6,6))
        self.lsw=0;self.ph=3;self.ld=None;self.smooth_amps=0
    def dec(self,s):
        d=D(pv=s.pv,netz=s.netz,batt=s.batt,wb=s.wb_leistung,pgrid=s.pgrid,car=s.wb_car)
        c=CFG
        # ── Car checks ──
        if s.wb_car in(0,1):
            d.action="WARTEN";d.reason="Auto voll"if s.wb_car==4 else"No car"
            self.pid.reset();self.ld=d;return d
        # ── Below minimum → stop ──
        min_p=c["goe"]["min_power_w"]
        if s.pgrid<min_p:
            d.action="STOP";d.reason=f"Uberschuss {s.pgrid:.0f}W < {min_p}W"
            self.pid.reset();self.smooth_amps=0;self.ld=d;return d
        # ── Phase switching ──
        pc=c["phase_switch"]
        can=(time.time()-self.lsw)>pc["min_switch_interval"]
        if can:
            if self.ph==1 and s.pgrid>=pc["hysteresis_up"]:
                self.ph=3;self.lsw=time.time();d.action="SW";d.reason=f"1->3ph (>={pc['hysteresis_up']}W)"
            elif self.ph==3 and s.pgrid<pc["hysteresis_down"]:
                self.ph=1;self.lsw=time.time();d.action="SW";d.reason=f"3->1ph (<{pc['hysteresis_down']}W)"
        d.target_ph=self.ph
        voltage=230; phases=d.target_ph
        # Direct amp from surplus (no PID)
        raw_amps=s.pgrid/(voltage*phases)
        # Clamp + round to even
        mn,mx=c["pid"]["output_limits"]
        raw_amps=max(mn,min(raw_amps,mx))
        raw_amps=round(raw_amps/2)*2  # even steps (6,8,10,12)
        raw_amps=max(mn,raw_amps)
        # Deadband
        if self.ld and d.action!="SW":
            if abs(raw_amps-self.ld.target_a)<c["deadband"]["amps"]:
                raw_amps=self.ld.target_a
        d.target_a=int(raw_amps)
        if d.action!="SW":
            est_load=d.target_a*voltage*phases
            d.action="CHG"
            d.reason=f"{d.target_a}A {d.target_ph}ph ~{est_load:.0f}W (Uberschuss {s.pgrid:.0f}W)"
        self.ld=d;return d

async def apply(sess,d):
    if SIM_MODE:
        act="WURDE"if d.action in("CHG","SW")else"NICHT"
        log.info(f"{act}:{d.action}|PV={d.pv:.0f}N={d.netz:.0f}B={d.batt:.0f}G={d.pgrid:.0f}|-{d.target_a}A/{d.target_ph}ph|{d.reason}")
        return
    # Only log & send if action/target changed (reduces log spam)
    this=(d.action, d.target_a, d.target_ph)
    changed = st.last_log != this
    if d.action=="SW":
        if changed and st.log_enabled: log.info(f"SW:{d.reason} → amp=0")
        await sess.get(f"http://{GOE_IP}/api/set?amp=0")
        await asyncio.sleep(0.5)
        if changed and st.log_enabled: log.info(f"SW:{d.reason} → psm={2 if d.target_ph==3 else 1}")
        await sess.get(f"http://{GOE_IP}/api/set?psm={2 if d.target_ph==3 else 1}")
    if d.target_a>0 and d.action!="STOP":
        if changed and st.log_enabled: log.info(f"SET:amp={d.target_a}A {d.target_ph}ph (PV={d.pv:.0f}W pGrid={d.pgrid:.0f}W)")
        await sess.get(f"http://{GOE_IP}/api/set?amp={d.target_a}")
    elif d.action=="STOP":
        if changed and st.log_enabled: log.info(f"STOP: pGrid={d.pgrid:.0f}W → amp=0")
        await sess.get(f"http://{GOE_IP}/api/set?amp=0")
    st.last_log=this

HISTORY_FILE = Path(CFG.get("history", {}).get("file", str(Path(__file__).parent / "history.json")))

class St:
    s=None;d=None;sim=SIM_MODE;run=True;err=0;cyc=0
    history=[]
    last_log=None
    log_enabled=True  # per Dashboard toggelbar
    _history_save_counter=0
st=St()

def _load_history():
    """Load persisted history from disk on startup."""
    try:
        if HISTORY_FILE.exists():
            data = json.loads(open(HISTORY_FILE).read())
            st.history = [h for h in data if time.time() - h["ts"] < 3600]
    except: pass

def _save_history():
    """Persist history to disk (called periodically)."""
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(st.history, f)
    except: pass

_load_history()

def _add_history(s,d):
    if d is None: return
    now=time.time()
    # Also capture go-e actual values from sensors
    wb_ist = st.s.wb_leistung if st.s else 0
    wb_amps_ist = st.s.wb_amps if st.s else 0
    st.history.append({"ts":now,"pv":d.pv,"pgrid":d.pgrid,
                       "target_a":d.target_a,"target_ph":d.target_ph,
                       "action":d.action,"wb_ist":wb_ist,"wb_amps_ist":wb_amps_ist})
    # Keep last ~60 min at 1 sample/sec = 3600 entries max
    cutoff=now-3600
    st.history=[h for h in st.history if h["ts"]>cutoff]

async def loop():
    ctrl=Ctrl()
    global _last_mb
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as sess:
        while st.run:
            try:
                s=await read_modbus()
                if s is None:
                    if _last_mb:
                        s=_last_mb
                        log.warning("Modbus-Ausfall, letzte Werte")
                    else:
                        st.err+=1; await asyncio.sleep(1); continue
                else:
                    _last_mb=s
                # Override wallbox data from go-e directly (more reliable)
                goe=await read_goe_direct(sess)
                if goe:
                    s.wb_leistung=goe["watt"]
                    s.wb_amps=goe["amp"]
                    s.wb_car=goe["car"]
                if s:st.s=s;d=ctrl.dec(s);st.d=d;_add_history(s,d);await apply(sess,d);st.cyc+=1
                else:st.err+=1
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                if not hasattr(st,'_last_err') or st._last_err != msg:
                    log.warning(f"Fehler: {msg}")
                    st._last_err = msg
                st.err+=1
            # Persist history every 30 cycles (~30s) so chart survives restarts
            st._history_save_counter += 1
            if st._history_save_counter >= 30:
                _save_history()
                st._history_save_counter = 0
            await asyncio.sleep(CFG["pid"]["sample_time"])
