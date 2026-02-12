"""
Dashboard Server — HTTP + WebSocket via aiohttp.

  http://localhost:8765      → Dashboard HTML page
  ws://localhost:8765/ws     → Live state broadcast
  http://localhost:8765/state → JSON snapshot

  python bot.py --bankroll 500 --dashboard
  → Open http://localhost:8765 in your browser
"""

import asyncio
import json
import logging
import time
from typing import Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger("dashboard")


class DashboardServer:
    def __init__(self, host="0.0.0.0", port=8765):
        self.host = host
        self.port = port
        self.clients: set[web.WebSocketResponse] = set()
        self._state: dict = {}
        self._running = False
        self._runner: Optional[web.AppRunner] = None

    async def start(self):
        app = web.Application()
        app.router.add_get("/", self._handle_page)
        app.router.add_get("/ws", self._handle_ws)
        app.router.add_get("/state", self._handle_state)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        self._running = True
        logger.info(f"Dashboard: http://localhost:{self.port}")

    async def _handle_page(self, request):
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def _handle_state(self, request):
        return web.json_response(self._state)

    async def _handle_ws(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.clients.add(ws)
        logger.info(f"Dashboard client connected ({len(self.clients)} total)")
        try:
            if self._state:
                await ws.send_json(self._state)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.PING:
                    await ws.pong(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            self.clients.discard(ws)
            logger.info(f"Dashboard client disconnected ({len(self.clients)} remaining)")
        return ws

    async def broadcast(self, state: dict):
        self._state = state
        dead = set()
        for ws in self.clients:
            try:
                await ws.send_json(state)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    async def stop(self):
        self._running = False
        for ws in list(self.clients):
            await ws.close()
        self.clients.clear()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Dashboard server stopped")

    @property
    def client_count(self):
        return len(self.clients)

    @property
    def is_running(self):
        return self._running


def build_dashboard_state(cycle, consensus, anchor, decision, risk_manager, polymarket_client, edge_config, config, arb_scanner=None):
    stats = polymarket_client.get_stats()
    risk_status = risk_manager.get_status()
    open_trades = polymarket_client.get_trade_records()

    signals = {}
    for s in (decision.signals if decision else []):
        signals[s.name] = {"direction": s.direction.value, "strength": round(s.strength, 3), "raw_value": round(s.raw_value, 4), "description": s.description}

    # Open positions
    open_pos = []
    for t in open_trades:
        if t.outcome is None:
            open_pos.append({"id": t.trade_id, "direction": t.direction, "size_usd": t.size_usd, "entry_price": t.entry_price, "confidence": t.confidence, "timestamp": t.timestamp, "oracle_price": t.oracle_price_at_entry})

    # Closed positions
    closed_pos = []
    for t in open_trades:
        if t.outcome is not None:
            closed_pos.append({"id": t.trade_id, "direction": t.direction, "size_usd": t.size_usd, "entry_price": t.entry_price, "confidence": t.confidence, "pnl": t.pnl, "outcome": t.outcome, "timestamp": t.timestamp})

    # Arb scanner stats
    arb_stats = arb_scanner.get_stats() if arb_scanner else None

    return {
        "type": "state", "timestamp": time.time(), "cycle": cycle,
        "oracle": {"price": consensus.price if consensus else 0, "chainlink": consensus.chainlink_price if consensus else None, "sources": consensus.sources if consensus else [], "spread_pct": consensus.spread_pct if consensus else 0},
        "anchor": {"open_price": anchor.open_price if anchor else None, "source": anchor.source if anchor else None, "drift_pct": decision.drift_pct if decision else None},
        "strategy": {"direction": decision.direction.value if decision else "hold", "confidence": decision.confidence if decision else 0, "should_trade": decision.should_trade if decision else False, "reason": decision.reason if decision else "", "drift_pct": decision.drift_pct if decision else None, "volatility_pct": decision.volatility_pct if decision else 0},
        "signals": signals,
        "stats": {"wins": stats.get("wins", 0), "losses": stats.get("losses", 0), "win_rate": stats.get("win_rate", 0), "total_pnl": stats.get("total_pnl", 0), "total_wagered": stats.get("total_wagered", 0), "total_trades": stats.get("total_trades", 0)},
        "risk": {"daily_trades": risk_status.get("daily_trades", 0), "max_daily_trades": config.risk.max_daily_trades, "daily_loss_pct": risk_status.get("daily_loss_pct", 0), "consecutive_losses": risk_status.get("consecutive_losses", 0), "cooldown_active": risk_status.get("cooldown_active", False)},
        "positions": {"open": open_pos, "closed": closed_pos[-50:]},
        "arb_scanner": arb_stats,
        "config": {"bankroll": config.bankroll, "arb_enabled": edge_config.enable_arb, "hedge_enabled": edge_config.enable_hedge},
    }


DASHBOARD_HTML = '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>BTC-15M-Oracle Dashboard</title>\n<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">\n<style>\n  *{box-sizing:border-box;margin:0;padding:0}\n  body{font-family:\'DM Sans\',sans-serif;background:#f8fafc;color:#1e293b}\n  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:2px}\n  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}\n  .mono{font-family:\'DM Mono\',monospace}\n  .card{background:#fff;border-radius:10px;border:1.5px solid #e2e8f0;padding:14px 16px}\n  .hero{background:linear-gradient(135deg,#16a34a 0%,#059669 40%,#0d9488 100%);padding:20px 28px 22px;color:#fff;position:relative;overflow:hidden}\n  .hero .c1{position:absolute;top:-40px;right:-40px;width:160px;height:160px;border-radius:50%;background:rgba(255,255,255,.06)}\n  .hero .c2{position:absolute;bottom:-30px;right:80px;width:100px;height:100px;border-radius:50%;background:rgba(255,255,255,.04)}\n  .sc{background:#fff;border-radius:10px;padding:16px 18px;position:relative;overflow:hidden}\n  .sc .ac{position:absolute;top:0;left:0;right:0;height:3px}\n  .sc .lb{font-size:11px;color:#94a3b8;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px}\n  .sc .vl{font-size:26px;font-weight:800;line-height:1.1}\n  .sc .sb{font-size:11px;color:#94a3b8;margin-top:6px}\n  .sr{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f1f5f9}\n  .si{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}\n  .su{background:#dcfce7;color:#16a34a}.sd{background:#fee2e2;color:#dc2626}\n  .g5{display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr 1fr;gap:10px;margin-bottom:16px}\n  .gm{display:grid;grid-template-columns:1fr 300px;gap:14px}\n  .hs{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px}\n  .hs>div{background:rgba(255,255,255,.12);border-radius:8px;padding:10px 14px;text-align:center}\n  .hs .hl{font-size:10px;opacity:.7;margin-bottom:2px}.hs .hv{font-size:18px;font-weight:800}\n  .er{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #f1f5f9}\n  .dot{width:8px;height:8px;border-radius:50%}\n  .bg{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px}\n  .db{font-size:12px;color:#64748b;line-height:1.7;background:#f8fafc;border-radius:6px;padding:12px;font-family:\'DM Mono\',monospace}\n  .db .k{color:#94a3b8}\n  .tab-btn{padding:6px 16px;border:none;background:none;border-radius:6px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:500;color:#94a3b8;transition:all .15s}\n  .tab-btn.active{background:#f1f5f9;font-weight:700;color:#1e293b}\n  .tbl-hdr{display:grid;grid-template-columns:56px 50px 1fr 56px 64px 56px;font-size:10px;font-weight:600;color:#94a3b8;padding:0 0 6px;border-bottom:1.5px solid #e2e8f0;letter-spacing:.03em;text-transform:uppercase}\n  .tbl-row{display:grid;grid-template-columns:56px 50px 1fr 56px 64px 56px;padding:8px 0;border-bottom:1px solid #f1f5f9;font-size:12px;align-items:center}\n  .dir-badge{width:18px;height:18px;border-radius:4px;display:inline-flex;align-items:center;justify-content:center;font-size:9px;font-weight:700}\n  .result-pill{font-weight:700;font-size:10px;padding:2px 6px;border-radius:4px;display:inline-block}\n  @media(max-width:900px){.g5{grid-template-columns:1fr 1fr}.gm{grid-template-columns:1fr}.hs{grid-template-columns:1fr 1fr}}\n</style>\n</head>\n<body>\n<div class="hero">\n  <div class="c1"></div><div class="c2"></div>\n  <div style="position:relative;z-index:1;max-width:1100px;margin:0 auto">\n    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:8px">\n      <div style="display:flex;align-items:center;gap:8px">\n        <span style="font-size:18px;font-weight:800">BTC-15M</span>\n        <span style="padding:2px 8px;border-radius:12px;background:rgba(255,255,255,.15);font-size:10px;font-weight:700;letter-spacing:.04em">ORACLE</span>\n        <div id="sb" style="display:flex;align-items:center;gap:4px;padding:2px 10px;border-radius:12px;background:rgba(255,200,50,.3);margin-left:6px">\n          <div id="sd" style="width:6px;height:6px;border-radius:50%;background:#fff"></div>\n          <span id="st" style="font-size:10px;font-weight:700">CONNECTING</span>\n        </div>\n      </div>\n      <div id="ci" style="font-size:11px;opacity:.7">Connecting to bot...</div>\n    </div>\n    <div style="text-align:center;margin-bottom:16px">\n      <div style="font-size:12px;font-weight:500;opacity:.8;margin-bottom:4px">Total Profit &middot; Cycle <span id="hc">0</span></div>\n      <div id="hp" style="font-size:42px;font-weight:800;line-height:1">$0.00</div>\n      <div style="font-size:12px;opacity:.7;margin-top:6px"><span id="ht">0</span> Bets &middot; $<span id="hw">0.00</span> Wagered</div>\n    </div>\n    <div class="hs">\n      <div><div class="hl">Win Rate</div><div class="hv" id="hwr">0%</div></div>\n      <div><div class="hl">BTC (Chainlink)</div><div class="hv" id="hbtc">$0</div></div>\n      <div><div class="hl">Next Entry</div><div class="hv" id="htm" style="color:#16a34a">--:--</div></div>\n      <div><div class="hl">Avg P&L / Bet</div><div class="hv" id="hav">$0.00</div></div>\n    </div>\n  </div>\n</div>\n<div style="max-width:1100px;margin:0 auto;padding:18px 24px">\n  <div class="g5">\n    <div class="sc" style="border:2px solid #bbf7d0"><div class="ac" style="background:#16a34a"></div><div class="lb">Bankroll</div><div class="vl" id="sb1">$1,000</div><div class="sb">Available balance</div></div>\n    <div class="sc" id="spc" style="border:2px solid #bbf7d0"><div class="ac" id="spa" style="background:#16a34a"></div><div class="lb">Realized P&L</div><div class="vl" id="sp" style="color:#16a34a">+$0.00</div><div class="sb" id="swl">0W &ndash; 0L</div></div>\n    <div class="sc" style="border:2px solid #c7d2fe"><div class="ac" style="background:#6366f1"></div><div class="lb">Window Open</div><div class="vl" id="so">&mdash;</div><div class="sb" id="sdr">Waiting...</div></div>\n    <div class="sc" style="border:2px solid #e2e8f0"><div class="lb">Strategy</div><div class="vl" id="sdi" style="color:#94a3b8">HOLD</div><div class="sb" id="scf">Confidence: 0%</div></div>\n    <div class="sc" style="border:2px solid #fde68a"><div class="ac" style="background:#f59e0b"></div><div class="lb">Daily Risk</div><div class="vl" id="sr" style="color:#f59e0b">0/20</div><div class="sb" id="ss">Streak: 0</div></div>\n  </div>\n  <div class="gm">\n    <div style="display:flex;flex-direction:column;gap:14px">\n      <!-- Equity -->\n      <div class="card"><div style="display:flex;justify-content:space-between;margin-bottom:8px"><span style="font-size:12px;font-weight:700;color:#334155">Equity Curve</span><span class="mono" style="font-size:12px;font-weight:700" id="ep">+$0.00</span></div><canvas id="ec" height="65" style="width:100%;display:block"></canvas></div>\n      <!-- Positions table -->\n      <div class="card" style="flex:1">\n        <div style="display:flex;gap:0;margin-bottom:10px">\n          <button class="tab-btn active" id="tab-open" onclick="switchTab(\'open\')">Open (<span id="open-count">0</span>)</button>\n          <button class="tab-btn" id="tab-closed" onclick="switchTab(\'closed\')">History (<span id="closed-count">0</span>)</button>\n        </div>\n        <div class="tbl-hdr">\n          <span>Time</span><span>Dir</span><span>Size</span><span style="text-align:right">Conf</span><span style="text-align:right">P&L</span><span style="text-align:right" id="col-last">Exp</span>\n        </div>\n        <div id="positions-body" style="max-height:240px;overflow-y:auto">\n          <div style="padding:32px;text-align:center;color:#94a3b8;font-size:12px">Waiting for entry window...</div>\n        </div>\n      </div>\n    </div>\n    <div style="display:flex;flex-direction:column;gap:14px">\n      <!-- Signals -->\n      <div class="card"><div style="font-size:12px;font-weight:700;color:#334155;margin-bottom:6px">Signals</div><div id="sg"><div style="padding:16px;text-align:center;color:#94a3b8;font-size:11px">Waiting for first cycle...</div></div><div style="margin-top:8px;font-size:10px;color:#94a3b8;line-height:1.5">Price vs Open: <span style="font-weight:700;color:#6366f1">35% weight</span> &middot; Chainlink BTC/USD</div></div>\n      <!-- Engines -->\n      <div class="card"><div style="font-size:12px;font-weight:700;color:#334155;margin-bottom:8px">Engines</div><div id="en"></div></div>\n      <!-- Oracle -->\n      <div class="card"><div style="font-size:12px;font-weight:700;color:#334155;margin-bottom:8px">Oracle Sources</div><div id="os"></div><div style="margin-top:6px;font-size:10px;color:#94a3b8">Spread: <span id="osp">0</span>%</div></div>\n    </div>\n  </div>\n  <div style="margin-top:16px;text-align:center;font-size:10px;color:#cbd5e1">BTC-15M-ORACLE v2.0 &middot; Chainlink resolution &middot; entries :59/:14/:29/:44</div>\n</div>\n<script>\nlet ws,state,eq=[1000],curTab=\'open\';\nfunction timer(){const n=new Date(),m=n.getMinutes(),s=n.getSeconds(),t=Math.max(0,(((Math.floor(m/15)+1)*15-m-1)*60+(60-s))%900-60);return{text:`${Math.floor(t/60)}:${String(t%60).padStart(2,\'0\')}`,secs:t,color:t<60?\'#dc2626\':t<240?\'#f59e0b\':\'#16a34a\'}}\n\nfunction switchTab(tab){\n  curTab=tab;\n  document.getElementById(\'tab-open\').className=\'tab-btn\'+(tab===\'open\'?\' active\':\'\');\n  document.getElementById(\'tab-closed\').className=\'tab-btn\'+(tab===\'closed\'?\' active\':\'\');\n  document.getElementById(\'col-last\').textContent=tab===\'closed\'?\'Result\':\'Exp\';\n  if(state)renderPositions(state);\n}\n\nfunction fmtTime(ts){if(!ts)return\'--:--\';const d=new Date(typeof ts===\'number\'?ts*1000:ts);return d.toLocaleTimeString([],{hour:\'2-digit\',minute:\'2-digit\',second:\'2-digit\'})}\nfunction fmtShortTime(ts){if(!ts)return\'--:--\';const d=new Date(typeof ts===\'number\'?ts*1000:ts);return d.toLocaleTimeString([],{hour:\'2-digit\',minute:\'2-digit\'})}\n\nfunction tradeRow(p,closed){\n  const up=p.direction===\'up\'||p.direction===\'UP\'||p.d===\'UP\';\n  const dir=(p.direction||p.d||\'\').toUpperCase();\n  const sz=p.size_usd||p.sz||0;\n  const ep=p.entry_price||p.ep||0;\n  const conf=p.confidence||p.conf||0;\n  const pnl=closed?(p.pnl||0):(p.uPnl||0);\n  const pc=pnl>=0?\'#16a34a\':\'#dc2626\';\n  const time=closed?fmtTime(p.timestamp||p.ct):fmtTime(p.timestamp||p.t);\n  let last=\'\';\n  if(closed){\n    const win=p.outcome===\'win\'||p.win===true||pnl>0;\n    last=`<span class="result-pill" style="background:${win?\'#dcfce7\':\'#fee2e2\'};color:${win?\'#16a34a\':\'#dc2626\'}">${win?\'WIN\':\'LOSS\'}</span>`;\n  }else{\n    last=`<span style="font-size:10px;color:#f59e0b;font-weight:600">${fmtShortTime(p.expiry||p.exp)}</span>`;\n  }\n  return `<div class="tbl-row">\n    <span style="color:#94a3b8">${time}</span>\n    <span style="display:inline-flex;align-items:center;gap:4px">\n      <span class="dir-badge" style="background:${up?\'#dcfce7\':\'#fee2e2\'};color:${up?\'#16a34a\':\'#dc2626\'}">${up?\'&#9650;\':\'&#9660;\'}</span>\n      <span style="font-weight:600;color:#334155;font-size:11px">${dir}</span>\n    </span>\n    <span style="color:#64748b">$${parseFloat(sz).toFixed(2)} <span style="color:#cbd5e1">@</span> ${parseFloat(ep).toFixed(3)}</span>\n    <span style="color:#94a3b8;text-align:right">${(conf*100).toFixed(0)}%</span>\n    <span style="font-weight:700;color:${pc};text-align:right">${pnl>=0?\'+\':\'\'}${parseFloat(pnl).toFixed(2)}</span>\n    <span style="text-align:right">${last}</span>\n  </div>`;\n}\n\nfunction renderPositions(d){\n  const op=d.positions?.open||[];\n  const cl=d.positions?.closed||[];\n  document.getElementById(\'open-count\').textContent=op.length;\n  document.getElementById(\'closed-count\').textContent=cl.length;\n  const body=document.getElementById(\'positions-body\');\n  const items=curTab===\'open\'?op:cl;\n  if(items.length===0){\n    body.innerHTML=`<div style="padding:32px;text-align:center;color:#94a3b8;font-size:12px">${curTab===\'open\'?\'Waiting for entry window...\':\'No history yet\'}</div>`;\n  }else{\n    body.innerHTML=items.map(p=>tradeRow(p,curTab===\'closed\')).join(\'\');\n  }\n}\n\nfunction drawEq(){const c=document.getElementById(\'ec\');if(!c)return;const x=c.getContext(\'2d\'),w=c.offsetWidth,h=65;c.width=w*2;c.height=h*2;x.scale(2,2);if(eq.length<2)return;const mn=Math.min(...eq),mx=Math.max(...eq),r=mx-mn||1,up=eq[eq.length-1]>=eq[0],cl=up?\'#16a34a\':\'#dc2626\';x.beginPath();x.moveTo(0,h);for(let i=0;i<eq.length;i++){x.lineTo((i/(eq.length-1))*w,h-((eq[i]-mn)/r)*(h-8)-4)}x.lineTo(w,h);x.closePath();const g=x.createLinearGradient(0,0,0,h);g.addColorStop(0,up?\'rgba(22,163,74,.15)\':\'rgba(220,38,38,.15)\');g.addColorStop(1,\'rgba(0,0,0,0)\');x.fillStyle=g;x.fill();x.beginPath();for(let i=0;i<eq.length;i++){const px=(i/(eq.length-1))*w,py=h-((eq[i]-mn)/r)*(h-8)-4;i===0?x.moveTo(px,py):x.lineTo(px,py)}x.strokeStyle=cl;x.lineWidth=2;x.stroke()}\n\nfunction render(d){if(!d)return;const p=d.stats?.total_pnl||0,pc=p>=0?\'#16a34a\':\'#dc2626\',wr=d.stats?.win_rate||0,bk=d.config?.bankroll||1000,dr=d.anchor?.drift_pct??d.strategy?.drift_pct,op=d.anchor?.open_price,t=timer();\neq.push(bk);if(eq.length>200)eq.shift();\ndocument.getElementById(\'hc\').textContent=d.cycle||0;document.getElementById(\'hp\').textContent=`${p>=0?\'+\':\'\'}$${p.toFixed(2)}`;document.getElementById(\'ht\').textContent=d.stats?.total_trades||0;document.getElementById(\'hw\').textContent=d.stats?.total_wagered||\'0.00\';document.getElementById(\'hwr\').textContent=`${wr}%`;document.getElementById(\'hbtc\').textContent=`$${parseFloat(d.oracle?.price||0).toLocaleString()}`;const te=document.getElementById(\'htm\');te.textContent=t.text;te.style.color=t.color;const av=d.stats?.total_trades>0?p/d.stats.total_trades:0;document.getElementById(\'hav\').textContent=`$${av.toFixed(2)}`;\ndocument.getElementById(\'sb1\').textContent=`$${typeof bk===\'number\'?bk.toFixed(2):bk}`;const se=document.getElementById(\'sp\');se.textContent=`${p>=0?\'+\':\'\'}$${p.toFixed(2)}`;se.style.color=pc;document.getElementById(\'spa\').style.background=pc;document.getElementById(\'spc\').style.borderColor=p>=0?\'#bbf7d0\':\'#fecaca\';document.getElementById(\'swl\').textContent=`${d.stats?.wins||0}W \\u2013 ${d.stats?.losses||0}L`;document.getElementById(\'so\').textContent=op?`$${parseFloat(op).toLocaleString()}`:\'\\u2014\';document.getElementById(\'sdr\').textContent=dr!=null?`Drift: ${dr>0?\'+\':\'\'}${parseFloat(dr).toFixed(4)}%`:\'Waiting...\';const di=document.getElementById(\'sdi\');di.textContent=(d.strategy?.direction||\'hold\').toUpperCase();di.style.color=d.strategy?.direction===\'up\'?\'#16a34a\':d.strategy?.direction===\'down\'?\'#dc2626\':\'#94a3b8\';document.getElementById(\'scf\').textContent=`Confidence: ${((d.strategy?.confidence||0)*100).toFixed(1)}%`;document.getElementById(\'sr\').textContent=`${d.risk?.daily_trades||0}/${d.risk?.max_daily_trades||20}`;document.getElementById(\'ss\').textContent=`Streak: ${d.risk?.consecutive_losses||0}`;\nconst epl=document.getElementById(\'ep\');epl.textContent=`${p>=0?\'+\':\'\'}$${p.toFixed(2)}`;epl.style.color=pc;drawEq();\n// Positions\nrenderPositions(d);\n// Signals\nconst sigs=d.signals||{},sk=Object.keys(sigs);if(sk.length>0){document.getElementById(\'sg\').innerHTML=sk.map(n=>{const s=sigs[n],u=s.direction===\'up\',c=u?\'#16a34a\':\'#dc2626\',l=n.replace(/_/g,\' \').replace(/\\b\\w/g,c=>c.toUpperCase()),x=n===\'rsi\'?`RSI: ${s.raw_value}`:n===\'price_vs_open\'?`Drift: ${s.raw_value>0?\'+\':\'\'}${s.raw_value}%`:\'\';return`<div class="sr"><div class="si ${u?\'su\':\'sd\'}">${u?\'\\u25b2\':\'\\u25bc\'}</div><div style="flex:1"><div style="font-size:12px;font-weight:600;color:#334155">${l}</div>${x?`<div style="font-size:10px;color:#94a3b8">${x}</div>`:\'\'}</div><div style="font-size:13px;font-weight:700;color:${c}">${s.direction.toUpperCase()} ${Math.round(s.strength*100)}%</div></div>`}).join(\'\')}\ndocument.getElementById(\'en\').innerHTML=[{n:\'Directional\',on:true,c:\'#16a34a\'},{n:\'Arbitrage\',on:d.config?.arb_enabled,c:\'#f59e0b\'},{n:\'Hedge\',on:d.config?.hedge_enabled,c:\'#6366f1\'}].map(m=>`<div class="er"><div class="dot" style="background:${m.on?m.c:\'#e2e8f0\'}"></div><span style="flex:1;font-size:12px;font-weight:500;color:${m.on?\'#334155\':\'#94a3b8\'}">${m.n}</span><span class="bg" style="background:${m.on?m.c+\'15\':\'#f1f5f9\'};color:${m.on?m.c:\'#94a3b8\'}">${m.on?\'ACTIVE\':\'OFF\'}</span></div>`).join(\'\');\ndocument.getElementById(\'os\').innerHTML=(d.oracle?.sources||[]).map(s=>`<div style="display:flex;align-items:center;gap:6px;padding:4px 0"><div class="dot" style="background:#16a34a;width:6px;height:6px"></div><span style="font-size:11px;color:#334155;font-weight:${s===\'chainlink\'?700:400}">${s}${s===\'chainlink\'?\' (resolution)\':\'\'}</span></div>`).join(\'\');\ndocument.getElementById(\'osp\').textContent=d.oracle?.spread_pct||0}\n\nfunction conn(){const p=location.protocol===\'https:\'?\'wss\':\'ws\';ws=new WebSocket(`${p}://${location.host}/ws`);ws.onopen=()=>{document.getElementById(\'sb\').style.background=\'rgba(255,255,255,.2)\';document.getElementById(\'sd\').style.animation=\'pulse 2s infinite\';document.getElementById(\'st\').textContent=\'LIVE\';document.getElementById(\'ci\').textContent=\'Connected\'};ws.onmessage=e=>{try{state=JSON.parse(e.data);render(state)}catch{}};ws.onclose=()=>{document.getElementById(\'sb\').style.background=\'rgba(255,200,50,.3)\';document.getElementById(\'sd\').style.animation=\'none\';document.getElementById(\'st\').textContent=\'RECONNECTING\';document.getElementById(\'ci\').textContent=\'Retrying...\';setTimeout(conn,3000)};ws.onerror=()=>ws.close()}\nsetInterval(()=>{const t=timer();const e=document.getElementById(\'htm\');if(e){e.textContent=t.text;e.style.color=t.color}},1000);\nconn();drawEq();\n</script>\n</body>\n</html>'
