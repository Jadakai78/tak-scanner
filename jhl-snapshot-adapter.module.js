export const CANONICAL_TOP_LEVEL_KEYS = [
  'meta',
  'session',
  'health',
  'regimes',
  'signals',
  'alerts',
  'diagnostics'
];

export function createCanonicalSnapshot(bus = {}, options = {}) {
  const nowIso = new Date().toISOString();
  const lastScan = bus.last_scan || null;
  const nextScan = bus.next_scan || null;
  const pairUniverseCount = Number(bus?.pair_universe?.count || 0);
  const activePairs = Number(bus?.active_pairs || pairUniverseCount || 0);
  const deadPairs = Number(bus?.dead_pairs || 0);
  const fgScore = Number(bus?.f_g?.score || 0);
  const fgLabel = bus?.f_g?.label || 'Unknown';
  const generatedAt = bus.generated_at || lastScan || nowIso;
  const source = options.source || 'signal_bus_adapter';
  const environment = options.environment || bus.environment || 'unknown';
  const version = options.version || 'v1';
  const latestRuntime = normalizeRuntime(bus);
  const integration = normalizeIntegration(bus);
  const notes = normalizeNotes(bus);

  const snapshot = {
    meta: {
      version,
      source,
      environment,
      generated_at: generatedAt
    },
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
      bus_health_pct: deriveHealthPct(bus),
      worker_push_ok: Boolean(bus?.worker_push_ok || false),
      bus_write_ok: Boolean(bus?.bus_write_ok || false),
      scan_duration_sec: Number(bus?.scan_duration_sec || 0),
      fear_greed: {
        score: fgScore,
        label: fgLabel
      }
    },
    regimes: normalizeRegimes(bus),
    signals: normalizeSignals(bus),
    alerts: normalizeAlerts(bus),
    diagnostics: {
      notes,
      latest_runtime: latestRuntime,
      integration
    }
  };

  return snapshot;
}

function normalizeRegimes(bus) {
  const regimemap = bus?.regimemap;
  if (!regimemap) return [];

  if (Array.isArray(regimemap)) {
    return regimemap
      .map((row) => ({ pair: row?.pair || row?.symbol || '', regime: row?.regime || row?.value || 'UNKNOWN' }))
      .filter((row) => row.pair);
  }

  if (typeof regimemap === 'object') {
    return Object.entries(regimemap).map(([pair, regime]) => ({ pair, regime: String(regime) }));
  }

  return [];
}

function normalizeSignals(bus) {
  const sourceSignals = Array.isArray(bus?.signals) ? bus.signals : [];
  return sourceSignals
    .map((row) => ({
      pair: row?.pair || row?.symbol || '',
      bias: row?.bias || 'LONG',
      grade: row?.grade || 'C',
      engine: row?.engine || 'unknown',
      conviction: Number(row?.conviction || 0),
      rr: Number(row?.rr || 0),
      entry: Number(row?.entry || 0),
      sl: Number(row?.sl || 0),
      tp: Number(row?.tp || 0),
      regime: row?.regime || 'UNKNOWN',
      intent: row?.intent || 'watch',
      prop: Boolean(row?.prop ?? row?.is_prop ?? false),
      mtf: row?.mtf || 'unknown'
    }))
    .filter((row) => row.pair);
}

function normalizeAlerts(bus) {
  const sourceAlerts = Array.isArray(bus?.alerts) ? bus.alerts : [];
  const killed = Array.isArray(bus?.killedsignals) ? bus.killedsignals : [];
  const killedAlerts = killed.map((row) => ({
    type: 'killed',
    pair: row?.pair || row?.symbol || 'unknown',
    reason: row?.reason || 'killed signal'
  }));
  return [...sourceAlerts, ...killedAlerts];
}

function normalizeRuntime(bus) {
  const rows = [];
  if (Array.isArray(bus?.latest_runtime)) rows.push(...bus.latest_runtime.map(String));
  if (bus?.worker_push_ok === false) rows.push('worker_push_ok=false');
  if (bus?.bus_write_ok === false) rows.push('bus_write_ok=false');
  return rows.slice(0, 20);
}

function normalizeIntegration(bus) {
  const rows = [];
  if (bus?.quiethours != null) rows.push('legacy quiethours present; keep in diagnostics only');
  if (bus?.sessionstats) rows.push('legacy sessionstats present; do not use as primary session model');
  if (bus?.regimemap) rows.push('legacy regimemap present; mapped into canonical regimes[]');
  return rows;
}

function normalizeNotes(bus) {
  const rows = [];
  if (Array.isArray(bus?.notes)) rows.push(...bus.notes.map(String));
  if (bus?.pair_universe && !Array.isArray(bus?.signals)) {
    rows.push('pair_universe is present; not all rows qualify as canonical signals');
  }
  return rows.slice(0, 20);
}

function deriveStatus(lastScan, nowIso) {
  if (!lastScan) return 'offline';
  const now = new Date(nowIso).getTime();
  const scan = new Date(lastScan).getTime();
  const diffMin = Math.abs(now - scan) / 60000;
  if (diffMin <= 30) return 'live';
  if (diffMin <= 180) return 'stale';
  return 'offline';
}

function deriveHealthPct(bus) {
  const checks = [
    bus?.last_scan != null,
    bus?.next_scan != null,
    bus?.f_g?.score != null,
    bus?.active_pairs != null,
    bus?.pair_universe?.count != null
  ];
  const passed = checks.filter(Boolean).length;
  return Math.round((passed / checks.length) * 100);
}
