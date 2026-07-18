export const CANONICAL_TOP_LEVEL_KEYS = [
  'meta', 'session', 'health', 'regimes', 'signals', 'alerts', 'diagnostics'
];

export function createCanonicalSnapshot(bus = {}, options = {}) {
  const nowIso = new Date().toISOString();
  const lastScan = bus.last_scan || null;
  const nextScan = bus.next_scan || null;
  const pairUniverseCount = Number(bus?.pair_universe?.count || 0);
  const activePairs = Number(bus?.active_pairs || pairUniverseCount || 0);
  const deadPairs = Number(bus?.dead_pairs || 0);

  // Handle both f_g (legacy) and fg (newer) field names
  const fgRaw = bus?.f_g || bus?.fg || {};
  const fgScore = Number(fgRaw?.score || 0);
  const fgLabel = fgRaw?.label || 'Unknown';

  const generatedAt = bus.generated_at || lastScan || nowIso;
  const source = options.source || 'signal_bus_adapter';
  const environment = options.environment || bus.environment || 'unknown';
  const version = options.version || 'v1';

  return {
    meta: { version, source, environment, generated_at: generatedAt },
    session: {
      last_scan: lastScan,
      next_scan: nextScan,
      active_pairs: activePairs,
      dead_pairs: deadPairs,
      pair_universe_count: pairUniverseCount,
      sprint_mode: Boolean(bus?.sprint_mode || false)
    },
    health: {
      status: deriveStatus(lastScan, options.now || nowIso),
      bus_health_pct: deriveHealthPct(bus, fgScore),
      worker_push_ok: Boolean(bus?.worker_push_ok || false),
      bus_write_ok: Boolean(bus?.bus_write_ok || false),
      scan_duration_sec: Number(bus?.scan_duration_sec || 0),
      fear_greed: { score: fgScore, label: fgLabel }
    },
    regimes: normalizeRegimes(bus),
    signals: normalizeSignals(bus),
    alerts: normalizeAlerts(bus),
    diagnostics: {
      notes: normalizeNotes(bus),
      latest_runtime: normalizeRuntime(bus),
      integration: normalizeIntegration(bus)
    },
    // Pass through raw pair universe for Kraken/Props tabs
    kraken_pairs: normalizePairUniverse(bus)
  };
}

function normalizeRegimes(bus) {
  // Handle both regime_map (with underscore) and regimemap (no underscore)
  const regimemap = bus?.regime_map || bus?.regimemap;
  if (!regimemap) return [];
  if (Array.isArray(regimemap)) {
    return regimemap
      .map(row => ({ pair: row?.pair || row?.symbol || '', regime: row?.regime || row?.value || 'UNKNOWN' }))
      .filter(row => row.pair);
  }
  if (typeof regimemap === 'object') {
    return Object.entries(regimemap).map(([pair, regime]) => ({ pair, regime: String(regime) }));
  }
  return [];
}

function normalizeSignals(bus) {
  const src = Array.isArray(bus?.signals) ? bus.signals : [];
  return src
    .filter(row => {
      const g = String(row?.grade || '').toUpperCase();
      return g === 'S' || g === 'A';  // S/A only on feed
    })
    .map(row => ({
      pair: row?.pair || row?.symbol || '',
      bias: row?.bias || 'LONG',
      grade: row?.grade || 'C',
      engine: row?.engine || 'unknown',
      conviction: Number(row?.conviction || 0),
      rr: Number(row?.rr || 0),
      entry: Number(row?.entry || 0),
      sl: Number(row?.sl || row?.kill_level || 0),
      tp: Number(row?.tp || 0),
      regime: row?.regime || 'UNKNOWN',
      intent: row?.intent || row?.action_state || 'WATCH',
      prop: Boolean(row?.prop ?? row?.is_prop ?? false),
      mtf: row?.mtf_verdict || row?.mtf || 'unknown',
      rts_family: row?.rts_family || null,
      offence: Number(row?.offence_score || 0),
      defence: Number(row?.defence_score || 0),
      trap: Number(row?.trap_score || 0)
    }))
    .filter(row => row.pair);
}

function normalizeAlerts(bus) {
  const src = Array.isArray(bus?.alerts) ? bus.alerts : [];
  // Handle both killed_signals (underscore) and killedsignals (no underscore)
  const killed = Array.isArray(bus?.killed_signals) ? bus.killed_signals
    : Array.isArray(bus?.killedsignals) ? bus.killedsignals : [];
  const killedAlerts = killed.map(row => ({
    type: 'killed',
    pair: row?.pair || row?.symbol || 'unknown',
    reason: row?.reason || row?.kill_reason || 'killed signal'
  }));
  return [...src, ...killedAlerts];
}

function normalizePairUniverse(bus) {
  // Support multiple shapes of pair universe
  if (Array.isArray(bus?.pair_universe)) return bus.pair_universe;
  if (Array.isArray(bus?.pair_universe?.rows)) return bus.pair_universe.rows;
  if (Array.isArray(bus?.kraken_pairs)) return bus.kraken_pairs;
  return [];
}

function normalizeRuntime(bus) {
  const rows = [];
  const stats = bus?.session_stats || {};
  if (stats.signals_fired != null) rows.push(`Signals fired: ${stats.signals_fired}`);
  if (stats.killed != null) rows.push(`Killed: ${stats.killed}`);
  if (stats.s_grade != null) rows.push(`S-grade: ${stats.s_grade}`);
  if (bus?.scan_duration_sec) rows.push(`Scan: ${Number(bus.scan_duration_sec).toFixed(1)}s`);
  if (Array.isArray(bus?.latest_runtime)) rows.push(...bus.latest_runtime.map(String));
  if (bus?.worker_push_ok === false) rows.push('WARNING: worker_push_ok=false');
  if (bus?.bus_write_ok === false) rows.push('WARNING: bus_write_ok=false');
  return rows.slice(0, 20);
}

function normalizeIntegration(bus) {
  const rows = [];
  if (bus?.quiet_hours != null || bus?.quiet_hours_active != null)
    rows.push('quiet_hours present — diagnostics only');
  if (bus?.session_stats) rows.push('session_stats mapped into diagnostics runtime');
  if (bus?.regime_map || bus?.regimemap) rows.push('regime_map normalized to canonical regimes[]');
  if (bus?.f_g) rows.push('f_g mapped to health.fear_greed');
  return rows;
}

function normalizeNotes(bus) {
  const rows = [];
  if (Array.isArray(bus?.notes)) rows.push(...bus.notes.map(String));
  return rows.slice(0, 20);
}

function deriveStatus(lastScan, nowIso) {
  if (!lastScan) return 'offline';
  const diffMin = Math.abs(new Date(nowIso) - new Date(lastScan)) / 60000;
  if (diffMin <= 30) return 'live';
  if (diffMin <= 180) return 'stale';
  return 'offline';
}

function deriveHealthPct(bus, fgScore) {
  const checks = [
    bus?.last_scan != null,
    bus?.next_scan != null,
    fgScore > 0,
    bus?.active_pairs != null,
    Array.isArray(bus?.signals) && bus.signals.length > 0
  ];
  return Math.round(checks.filter(Boolean).length / checks.length * 100);
}
