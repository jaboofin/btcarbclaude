import { useState, useEffect, useRef, useCallback } from "react";

// ── WebSocket Hook ────────────────────────────────────────────
function useBot(url = "ws://localhost:8765") {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef(null);
  const retryRef = useRef(null);

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => { setConnected(true); console.log("Dashboard connected"); };
      ws.onmessage = (e) => {
        try { setState(JSON.parse(e.data)); } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        retryRef.current = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
    } catch { retryRef.current = setTimeout(connect, 3000); }
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(retryRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connect]);

  return { state, connected };
}

// ── Simulated fallback ────────────────────────────────────────
function useSim() {
  const [d, setD] = useState(null);
  useEffect(() => {
    const gen = () => {
      const btc = 97400 + (Math.random() - 0.48) * 200;
      const wo = btc - (Math.random() - 0.5) * 100;
      const drift = ((btc - wo) / wo) * 100;
      const w = Math.floor(Math.random() * 8), l = Math.floor(Math.random() * 5);
      const pnl = (w * 8 - l * 10 + Math.random() * 30 - 10);
      const sig = () => ({ direction: Math.random() > 0.5 ? "up" : "down", strength: +(Math.random() * 0.8 + 0.2).toFixed(3), raw_value: +(Math.random() * 2 - 1).toFixed(4) });
      const min = new Date().getMinutes(), sec = new Date().getSeconds();
      const secs = Math.max(0, (((Math.floor(min / 15) + 1) * 15 - min - 1) * 60 + (60 - sec)) % 900 - 60);
      return {
        cycle: Math.floor(Math.random() * 50),
        oracle: { price: +btc.toFixed(2), chainlink: +(btc + Math.random() * 3).toFixed(2), sources: ["chainlink", "binance", "coingecko"], spread_pct: +(Math.random() * 0.03).toFixed(4) },
        anchor: { open_price: +wo.toFixed(2), source: "chainlink", drift_pct: +drift.toFixed(4) },
        strategy: { direction: drift > 0 ? "up" : "down", confidence: +(0.55 + Math.random() * 0.4).toFixed(3), should_trade: Math.random() > 0.3, drift_pct: +drift.toFixed(4) },
        signals: { price_vs_open: { direction: drift > 0 ? "up" : "down", strength: +Math.min(1, Math.abs(drift) / 0.2).toFixed(3), raw_value: +drift.toFixed(4) }, momentum: sig(), rsi: { ...sig(), raw_value: +(30 + Math.random() * 40).toFixed(1) }, macd: sig(), ema_cross: sig() },
        stats: { wins: w, losses: l, win_rate: w + l > 0 ? +((w / (w + l)) * 100).toFixed(1) : 0, total_pnl: +pnl.toFixed(2), total_wagered: +((w + l) * 15).toFixed(2), total_trades: w + l },
        risk: { daily_trades: w + l, max_daily_trades: 20, consecutive_losses: Math.floor(Math.random() * 3) },
        config: { bankroll: 1000 + pnl, arb_enabled: true, hedge_enabled: false },
        _secs: secs,
      };
    };
    setD(gen());
    const iv = setInterval(() => setD(gen()), 4000);
    return () => clearInterval(iv);
  }, []);
  return d;
}

// ── Spark ─────────────────────────────────────────────────────
function Spark({ values, w = 500, h = 60 }) {
  if (!values || values.length < 2) return null;
  const mn = Math.min(...values), mx = Math.max(...values), rng = mx - mn || 1;
  const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - mn) / rng) * (h - 8) - 4}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  const c = up ? "#16a34a" : "#dc2626";
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ display: "block" }}>
      <defs><linearGradient id="sf" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={c} stopOpacity=".15" /><stop offset="100%" stopColor={c} stopOpacity="0" /></linearGradient></defs>
      <polygon points={`0,${h} ${pts} ${w},${h}`} fill="url(#sf)" />
      <polyline points={pts} fill="none" stroke={c} strokeWidth="2" />
    </svg>
  );
}

// ── Stat Card ─────────────────────────────────────────────────
function SC({ label, value, sub, color = "#1e293b", accent, border }) {
  return (
    <div style={{ background: "#fff", borderRadius: 10, padding: "16px 18px", border: `2px solid ${border || "#e2e8f0"}`, position: "relative", overflow: "hidden" }}>
      {accent && <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: accent }} />}
      <div style={{ fontSize: 11, color: "#94a3b8", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase", marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, color, lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

// ── Signal Row ────────────────────────────────────────────────
function SigRow({ label, direction, strength, extra }) {
  const up = direction === "up";
  const c = up ? "#16a34a" : "#dc2626";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: "1px solid #f1f5f9" }}>
      <div style={{ width: 28, height: 28, borderRadius: 6, background: up ? "#dcfce7" : "#fee2e2", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, color: c, flexShrink: 0 }}>{up ? "▲" : "▼"}</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: "#334155" }}>{label}</div>
        {extra && <div style={{ fontSize: 10, color: "#94a3b8" }}>{extra}</div>}
      </div>
      <div style={{ fontSize: 13, fontWeight: 700, color: c }}>{direction.toUpperCase()} {Math.round(strength * 100)}%</div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────
export default function Dashboard() {
  const { state: live, connected } = useBot();
  const sim = useSim();
  const d = live || sim;
  const [eqHistory, setEqHistory] = useState([1000]);

  // Track equity over time
  useEffect(() => {
    if (!d) return;
    const bankroll = d.config?.bankroll || 1000;
    setEqHistory(prev => {
      const next = [...prev, bankroll];
      return next.length > 200 ? next.slice(-200) : next;
    });
  }, [d?.cycle]);

  if (!d) return <div style={{ padding: 40, textAlign: "center", color: "#94a3b8" }}>Connecting...</div>;

  const pnl = d.stats?.total_pnl || 0;
  const pnlC = pnl >= 0 ? "#16a34a" : "#dc2626";
  const wr = d.stats?.win_rate || 0;
  const bankroll = d.config?.bankroll || 1000;
  const openPrice = d.anchor?.open_price;
  const drift = d.anchor?.drift_pct || d.strategy?.drift_pct;
  const min = new Date().getMinutes(), sec = new Date().getSeconds();
  const secs = d._secs ?? Math.max(0, (((Math.floor(min / 15) + 1) * 15 - min - 1) * 60 + (60 - sec)) % 900 - 60);
  const tm = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  const tmC = secs < 60 ? "#dc2626" : secs < 240 ? "#f59e0b" : "#16a34a";

  return (
    <div style={{ fontFamily: "'DM Sans', 'Nunito', sans-serif", background: "#f8fafc", minHeight: "100vh", color: "#1e293b" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=DM+Mono:wght@400;500&display=swap');
        * { box-sizing: border-box; margin: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 2px; }
        @keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }
      `}</style>

      {/* ── Hero ── */}
      <div style={{ background: "linear-gradient(135deg, #16a34a 0%, #059669 40%, #0d9488 100%)", padding: "20px 28px 22px", color: "#fff", position: "relative", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: -40, right: -40, width: 160, height: 160, borderRadius: "50%", background: "rgba(255,255,255,0.06)" }} />
        <div style={{ position: "absolute", bottom: -30, right: 80, width: 100, height: 100, borderRadius: "50%", background: "rgba(255,255,255,0.04)" }} />

        <div style={{ position: "relative", zIndex: 1, maxWidth: 1100, margin: "0 auto" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 18, fontWeight: 800 }}>BTC-15M</span>
              <span style={{ padding: "2px 8px", borderRadius: 12, background: "rgba(255,255,255,0.15)", fontSize: 10, fontWeight: 700, letterSpacing: ".04em" }}>ORACLE</span>
              <div style={{ display: "flex", alignItems: "center", gap: 4, padding: "2px 10px", borderRadius: 12, background: connected ? "rgba(255,255,255,0.2)" : "rgba(255,200,50,0.3)", marginLeft: 6 }}>
                <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#fff", animation: connected ? "pulse 2s infinite" : "none" }} />
                <span style={{ fontSize: 10, fontWeight: 700 }}>{connected ? "LIVE" : "SIMULATED"}</span>
              </div>
            </div>
            <div style={{ fontSize: 11, opacity: 0.7 }}>
              {connected ? "ws://localhost:8765" : "No bot connection — showing demo data"}
            </div>
          </div>

          <div style={{ textAlign: "center", marginBottom: 16 }}>
            <div style={{ fontSize: 12, fontWeight: 500, opacity: 0.8, marginBottom: 4 }}>Total Profit · Cycle {d.cycle || 0}</div>
            <div style={{ fontSize: 42, fontWeight: 800, lineHeight: 1 }}>{pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}</div>
            <div style={{ fontSize: 12, opacity: 0.7, marginTop: 6 }}>{d.stats?.total_trades || 0} Bets · ${d.stats?.total_wagered || "0.00"} Wagered</div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 8 }}>
            {[
              { l: "Win Rate", v: `${wr}%` },
              { l: "BTC (Chainlink)", v: `$${parseFloat(d.oracle?.price || 0).toLocaleString()}` },
              { l: "Next Entry", v: tm, c: tmC },
              { l: "Avg P&L / Bet", v: d.stats?.total_trades > 0 ? `$${(pnl / d.stats.total_trades).toFixed(2)}` : "$0.00" },
            ].map(x => (
              <div key={x.l} style={{ background: "rgba(255,255,255,0.12)", borderRadius: 8, padding: "10px 14px", textAlign: "center" }}>
                <div style={{ fontSize: 10, opacity: 0.7, marginBottom: 2 }}>{x.l}</div>
                <div style={{ fontSize: 18, fontWeight: 800, color: x.c || "#fff" }}>{x.v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── Body ── */}
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "18px 24px" }}>

        {/* Stats */}
        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr 1fr 1fr 1fr", gap: 10, marginBottom: 16 }}>
          <SC label="Bankroll" value={`$${bankroll.toFixed ? bankroll.toFixed(2) : bankroll}`} sub="Available balance" accent="#16a34a" border="#bbf7d0" />
          <SC label="Realized P&L" value={`${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`} color={pnlC} sub={`${d.stats?.wins || 0}W – ${d.stats?.losses || 0}L`} accent={pnlC} border={pnl >= 0 ? "#bbf7d0" : "#fecaca"} />
          <SC label="Window Open" value={openPrice ? `$${parseFloat(openPrice).toLocaleString()}` : "—"} sub={drift != null ? `Drift: ${drift > 0 ? "+" : ""}${parseFloat(drift).toFixed(4)}%` : "Waiting..."} accent="#6366f1" border="#c7d2fe" />
          <SC label="Strategy" value={d.strategy?.direction?.toUpperCase() || "HOLD"} color={d.strategy?.direction === "up" ? "#16a34a" : d.strategy?.direction === "down" ? "#dc2626" : "#94a3b8"} sub={`Confidence: ${((d.strategy?.confidence || 0) * 100).toFixed(1)}%`} border="#e2e8f0" />
          <SC label="Daily Risk" value={`${d.risk?.daily_trades || 0}/${d.risk?.max_daily_trades || 20}`} color="#f59e0b" sub={`Streak: ${d.risk?.consecutive_losses || 0}`} accent="#f59e0b" border="#fde68a" />
        </div>

        {/* Main grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 300px", gap: 14 }}>
          {/* Left */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {/* Equity */}
            <div style={{ background: "#fff", borderRadius: 10, border: "1.5px solid #e2e8f0", padding: "14px 16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "#334155" }}>Equity Curve</span>
                <span style={{ fontSize: 12, fontWeight: 700, fontFamily: "'DM Mono'", color: pnlC }}>{pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}</span>
              </div>
              <Spark values={eqHistory} />
            </div>

            {/* Decision log */}
            <div style={{ background: "#fff", borderRadius: 10, border: "1.5px solid #e2e8f0", padding: "14px 16px", flex: 1 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 8 }}>Last Decision</div>
              <div style={{ fontSize: 12, color: "#64748b", lineHeight: 1.7, fontFamily: "'DM Mono'", background: "#f8fafc", borderRadius: 6, padding: 12 }}>
                <div><span style={{ color: "#94a3b8" }}>direction:</span> <span style={{ fontWeight: 700, color: d.strategy?.direction === "up" ? "#16a34a" : d.strategy?.direction === "down" ? "#dc2626" : "#64748b" }}>{d.strategy?.direction || "hold"}</span></div>
                <div><span style={{ color: "#94a3b8" }}>confidence:</span> {((d.strategy?.confidence || 0) * 100).toFixed(1)}%</div>
                <div><span style={{ color: "#94a3b8" }}>should_trade:</span> <span style={{ color: d.strategy?.should_trade ? "#16a34a" : "#dc2626" }}>{String(d.strategy?.should_trade ?? false)}</span></div>
                <div><span style={{ color: "#94a3b8" }}>drift_from_open:</span> {drift != null ? `${drift > 0 ? "+" : ""}${parseFloat(drift).toFixed(4)}%` : "n/a"}</div>
                <div><span style={{ color: "#94a3b8" }}>btc_price:</span> ${parseFloat(d.oracle?.price || 0).toLocaleString()}</div>
                <div><span style={{ color: "#94a3b8" }}>chainlink:</span> {d.oracle?.chainlink ? `$${parseFloat(d.oracle.chainlink).toLocaleString()}` : "—"}</div>
                <div><span style={{ color: "#94a3b8" }}>oracle_spread:</span> {d.oracle?.spread_pct || 0}%</div>
                {d.strategy?.reason && <div style={{ marginTop: 4, color: "#94a3b8", fontSize: 10 }}>{d.strategy.reason}</div>}
              </div>
            </div>
          </div>

          {/* Right */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            {/* Signals */}
            <div style={{ background: "#fff", borderRadius: 10, border: "1.5px solid #e2e8f0", padding: "14px 16px" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 6 }}>Signals</div>
              {d.signals && Object.entries(d.signals).map(([name, s]) => (
                <SigRow key={name}
                  label={name.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                  direction={s.direction} strength={s.strength}
                  extra={name === "rsi" ? `RSI: ${s.raw_value}` : name === "price_vs_open" ? `Drift: ${s.raw_value > 0 ? "+" : ""}${s.raw_value}%` : null}
                />
              ))}
              {(!d.signals || Object.keys(d.signals).length === 0) && <div style={{ padding: 16, textAlign: "center", color: "#94a3b8", fontSize: 11 }}>Waiting for first cycle...</div>}
              <div style={{ marginTop: 8, fontSize: 10, color: "#94a3b8", lineHeight: 1.5 }}>
                Price vs Open: <span style={{ fontWeight: 700, color: "#6366f1" }}>35% weight</span> · Chainlink BTC/USD
              </div>
            </div>

            {/* Engines */}
            <div style={{ background: "#fff", borderRadius: 10, border: "1.5px solid #e2e8f0", padding: "14px 16px" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 8 }}>Engines</div>
              {[
                { n: "Directional", on: true, c: "#16a34a" },
                { n: "Arbitrage", on: d.config?.arb_enabled, c: "#f59e0b" },
                { n: "Hedge", on: d.config?.hedge_enabled, c: "#6366f1" },
              ].map(m => (
                <div key={m.n} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderBottom: "1px solid #f1f5f9" }}>
                  <div style={{ width: 8, height: 8, borderRadius: "50%", background: m.on ? m.c : "#e2e8f0" }} />
                  <span style={{ flex: 1, fontSize: 12, fontWeight: 500, color: m.on ? "#334155" : "#94a3b8" }}>{m.n}</span>
                  <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 4, background: m.on ? `${m.c}15` : "#f1f5f9", color: m.on ? m.c : "#94a3b8" }}>{m.on ? "ACTIVE" : "OFF"}</span>
                </div>
              ))}
            </div>

            {/* Oracle sources */}
            <div style={{ background: "#fff", borderRadius: 10, border: "1.5px solid #e2e8f0", padding: "14px 16px" }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 8 }}>Oracle Sources</div>
              {(d.oracle?.sources || []).map(s => (
                <div key={s} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 0" }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#16a34a" }} />
                  <span style={{ fontSize: 11, color: "#334155", fontWeight: s === "chainlink" ? 700 : 400 }}>{s}{s === "chainlink" ? " (resolution)" : ""}</span>
                </div>
              ))}
              <div style={{ marginTop: 6, fontSize: 10, color: "#94a3b8" }}>Spread: {d.oracle?.spread_pct || 0}%</div>
            </div>
          </div>
        </div>

        <div style={{ marginTop: 16, textAlign: "center", fontSize: 10, color: "#cbd5e1" }}>
          BTC-15M-ORACLE v2.0 · {connected ? "🟢 Live" : "🟡 Simulated"} · Chainlink resolution · entries :59/:14/:29/:44
        </div>
      </div>
    </div>
  );
}
