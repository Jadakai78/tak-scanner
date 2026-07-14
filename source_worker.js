export default {
  async fetch(request, env, ctx) {
    try {
      const url = new URL(request.url);
      const method = request.method.toUpperCase();

      if (method === 'GET' && url.pathname === '/') {
        return json({
          ok: true,
          service: 'source-worker',
          version: 'v1',
          routes: {
            health: 'GET /health',
            emit: 'POST /emit',
            events: 'GET /events?since=ISO8601&limit=50&type=signal|order|status&account=PropOne'
          }
        });
      }

      if (method === 'GET' && url.pathname === '/health') {
        return json({ ok: true, service: 'source-worker', version: 'v1', ts: new Date().toISOString() });
      }

      if (method === 'POST' && url.pathname === '/emit') {
        authorize(request, env);
        const body = await request.json();
        const event = normalizeIncomingEvent(body);
        await storeEvent(env, event);
        return json({ ok: true, stored: true, event_id: event.event_id, event_type: event.event_type });
      }

      if (method === 'GET' && url.pathname === '/events') {
        authorize(request, env, false);
        const since = url.searchParams.get('since');
        const limit = clampInt(url.searchParams.get('limit'), 50, 1, 200);
        const type = url.searchParams.get('type');
        const account = url.searchParams.get('account');
        const pair = url.searchParams.get('pair');

        const results = await listEvents(env, { since, limit, type, account, pair });
        return json({ ok: true, count: results.length, events: results });
      }

      return json({ ok: false, error: 'not_found' }, 404);
    } catch (err) {
      return json({ ok: false, error: err.message || 'unknown_error' }, err.status || 500);
    }
  }
};

function authorize(request, env, required = true) {
  const configured = env.SOURCE_SECRET;
  if (!configured && !required) return;
  if (!configured && required) throw httpError('missing SOURCE_SECRET in env', 500);
  const supplied = request.headers.get('x-source-secret') || request.headers.get('authorization')?.replace(/^Bearer\s+/i, '');
  if (supplied !== configured) throw httpError('unauthorized', 401);
}

function normalizeIncomingEvent(input) {
  if (!input || typeof input !== 'object') throw httpError('invalid_json_body', 400);

  const eventType = String(input.event_type || '').trim();
  if (!['signal', 'order', 'status'].includes(eventType)) throw httpError('event_type must be signal, order, or status', 400);

  const now = new Date().toISOString();
  const ts = parseIso(input.ts || now);
  const account = stringOr(input.account, eventType === 'status' ? 'global' : 'unknown');
  const pair = input.pair == null ? null : String(input.pair).trim();
  const payload = input.payload && typeof input.payload === 'object' ? input.payload : {};

  const event = {
    event_id: stringOr(input.event_id, buildEventId(eventType, account, pair, ts)),
    event_type: eventType,
    ts,
    account,
    pair,
    headline: stringOr(input.headline, buildHeadline(eventType, pair, payload)),
    priority: normalizePriority(input.priority, eventType, payload),
    channel_targets: normalizeTargets(input.channel_targets, eventType),
    payload: normalizePayload(eventType, payload)
  };

  validateEvent(event);
  return event;
}

function normalizePayload(eventType, payload) {
  if (eventType === 'signal') {
    return {
      action: reqStr(payload.action, 'payload.action'),
      bias: reqStr(payload.bias, 'payload.bias'),
      grade: reqStr(payload.grade, 'payload.grade'),
      engine: reqStr(payload.engine, 'payload.engine'),
      entry: reqNum(payload.entry, 'payload.entry'),
      stop: reqNum(payload.stop, 'payload.stop'),
      target: reqNum(payload.target, 'payload.target'),
      rr: reqNum(payload.rr, 'payload.rr'),
      risk_pct: reqNum(payload.risk_pct, 'payload.risk_pct'),
      reason_short: reqStr(payload.reason_short, 'payload.reason_short'),
      reason_full: reqStr(payload.reason_full, 'payload.reason_full'),
      tags: Array.isArray(payload.tags) ? payload.tags.map(String) : []
    };
  }

  if (eventType === 'order') {
    return {
      order_action: reqStr(payload.order_action, 'payload.order_action'),
      bias: reqStr(payload.bias, 'payload.bias'),
      entry: reqNum(payload.entry, 'payload.entry'),
      exit: numOrNull(payload.exit),
      stop: reqNum(payload.stop, 'payload.stop'),
      target: reqNum(payload.target, 'payload.target'),
      pnl: reqNum(payload.pnl, 'payload.pnl'),
      reason_short: reqStr(payload.reason_short, 'payload.reason_short'),
      reason_full: reqStr(payload.reason_full, 'payload.reason_full'),
      linked_signal_id: reqStr(payload.linked_signal_id, 'payload.linked_signal_id')
    };
  }

  return {
    status_mode: reqStr(payload.status_mode, 'payload.status_mode'),
    scope: reqStr(payload.scope, 'payload.scope'),
    reason_short: reqStr(payload.reason_short, 'payload.reason_short'),
    reason_full: reqStr(payload.reason_full, 'payload.reason_full'),
    until: payload.until == null ? null : parseIso(payload.until),
    counts: {
      s_grade_count: intOr(payload.counts?.s_grade_count, 0),
      a_grade_count: intOr(payload.counts?.a_grade_count, 0),
      pairs_active: intOr(payload.counts?.pairs_active, 0)
    }
  };
}

function validateEvent(event) {
  if (!event.event_id) throw httpError('event_id missing', 400);
  if (!event.headline) throw httpError('headline missing', 400);
  if (!Array.isArray(event.channel_targets) || event.channel_targets.length === 0) throw httpError('channel_targets missing', 400);
}

async function storeEvent(env, event) {
  if (!env.EVENTS_KV) throw httpError('missing EVENTS_KV binding', 500);

  const key = `event:${event.ts}:${event.event_id}`;
  await env.EVENTS_KV.put(key, JSON.stringify(event));

  const latestKey = `latest:${event.event_type}:${event.account}:${event.pair || 'none'}`;
  await env.EVENTS_KV.put(latestKey, JSON.stringify(event));
}

async function listEvents(env, filters) {
  if (!env.EVENTS_KV) throw httpError('missing EVENTS_KV binding', 500);

  const list = await env.EVENTS_KV.list({ prefix: 'event:' });
  const out = [];

  for (const k of list.keys) {
    const raw = await env.EVENTS_KV.get(k.name);
    if (!raw) continue;
    let event;
    try { event = JSON.parse(raw); } catch { continue; }

    if (filters.since && event.ts < filters.since) continue;
    if (filters.type && event.event_type !== filters.type) continue;
    if (filters.account && event.account !== filters.account) continue;
    if (filters.pair && event.pair !== filters.pair) continue;

    out.push(event);
  }

  out.sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
  return out.slice(0, filters.limit);
}

function buildHeadline(eventType, pair, payload) {
  if (eventType === 'signal') return `${pair || 'UNKNOWN'} · ${payload.action || 'WAIT'} · ${payload.grade || '?'}-grade ${payload.engine || 'engine'}`;
  if (eventType === 'order') return `${pair || 'UNKNOWN'} · ${payload.order_action || 'UPDATED'}`;
  if (eventType === 'status') {
    const sg = intOr(payload.counts?.s_grade_count, 0);
    const ag = intOr(payload.counts?.a_grade_count, 0);
    return `${payload.status_mode || 'STATUS'} · ${sg} S-grade, ${ag} A-grade`;
  }
  return 'EVENT';
}

function buildEventId(eventType, account, pair, ts) {
  const base = [eventType, account || 'global', pair || 'none', ts].join('_');
  return slug(base);
}

function normalizeTargets(targets, eventType) {
  if (Array.isArray(targets) && targets.length) return [...new Set(targets.map(String))];
  if (eventType === 'order') return ['telegram', 'email'];
  return ['pushover', 'telegram', 'email'];
}

function normalizePriority(priority, eventType, payload) {
  const p = String(priority || '').toLowerCase();
  if (['low', 'normal', 'high', 'critical'].includes(p)) return p;
  if (eventType === 'status' && ['STAND_DOWN', 'TIME_TO_HUNT'].includes(String(payload.status_mode || ''))) return 'high';
  if (eventType === 'signal' && String(payload.grade || '') === 'S') return 'high';
  return 'normal';
}

function reqStr(v, name) {
  const s = String(v ?? '').trim();
  if (!s) throw httpError(`${name} missing`, 400);
  return s;
}

function reqNum(v, name) {
  const n = Number(v);
  if (!Number.isFinite(n)) throw httpError(`${name} missing or invalid`, 400);
  return n;
}

function numOrNull(v) {
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function intOr(v, fallback) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : fallback;
}

function stringOr(v, fallback) {
  const s = String(v ?? '').trim();
  return s || fallback;
}

function parseIso(v) {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) throw httpError('invalid ISO timestamp', 400);
  return d.toISOString();
}

function clampInt(v, fallback, min, max) {
  const n = Number(v);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

function slug(s) {
  return String(s).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
}

function httpError(message, status) {
  const err = new Error(message);
  err.status = status;
  return err;
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store'
    }
  });
}
