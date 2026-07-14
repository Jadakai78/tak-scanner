export default {
  async fetch(request, env, ctx) {
    try {
      const url = new URL(request.url);
      const method = request.method.toUpperCase();

      if (method === 'GET' && url.pathname === '/') {
        return json({
          ok: true,
          service: 'telegram-worker',
          version: 'v1',
          routes: {
            health: 'GET /health',
            poll: 'GET /poll',
            webhook: 'POST /telegram/webhook'
          }
        });
      }

      if (method === 'GET' && url.pathname === '/health') {
        return json({ ok: true, service: 'telegram-worker', version: 'v1', ts: new Date().toISOString() });
      }

      if (method === 'GET' && url.pathname === '/poll') {
        authorize(request, env);
        const sent = await runPoll(env);
        return json({ ok: true, sent_count: sent.count, sent_event_ids: sent.ids, cursor: sent.cursor });
      }

      if (method === 'POST' && url.pathname === '/telegram/webhook') {
        const update = await request.json();
        const reply = await handleWebhook(update, env);
        return json({ ok: true, reply });
      }

      return json({ ok: false, error: 'not_found' }, 404);
    } catch (err) {
      return json({ ok: false, error: err.message || 'unknown_error' }, err.status || 500);
    }
  },

  async scheduled(controller, env, ctx) {
    await runPoll(env);
  }
};

async function runPoll(env) {
  requireEnv(env, ['SOURCE_URL', 'SOURCE_SECRET', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'TELEGRAM_STATE_KV']);

  const since = (await env.TELEGRAM_STATE_KV.get('telegram:last_ts')) || '';
  const url = new URL('/events', env.SOURCE_URL);
  if (since) url.searchParams.set('since', since);
  url.searchParams.set('limit', '100');

  const res = await fetch(url.toString(), {
    headers: {
      'x-source-secret': env.SOURCE_SECRET
    }
  });

  if (!res.ok) {
    throw httpError(`source fetch failed: ${res.status}`, 502);
  }

  const data = await res.json();
  const events = Array.isArray(data.events) ? data.events : [];
  const ordered = [...events].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));

  const sentIds = [];
  let latestTs = since;

  for (const event of ordered) {
    if (!shouldSendToTelegram(event)) continue;
    const dedupeKey = `telegram:sent:${event.event_id}`;
    const already = await env.TELEGRAM_STATE_KV.get(dedupeKey);
    if (already) {
      if (!latestTs || event.ts > latestTs) latestTs = event.ts;
      continue;
    }

    const text = formatTelegramMessage(event);
    await sendTelegramMessage(env, text);
    await env.TELEGRAM_STATE_KV.put(dedupeKey, event.ts);
    sentIds.push(event.event_id);
    if (!latestTs || event.ts > latestTs) latestTs = event.ts;
  }

  if (latestTs) {
    await env.TELEGRAM_STATE_KV.put('telegram:last_ts', latestTs);
  }

  return { count: sentIds.length, ids: sentIds, cursor: latestTs || null };
}

function shouldSendToTelegram(event) {
  return Array.isArray(event?.channel_targets) && event.channel_targets.includes('telegram');
}

function formatTelegramMessage(event) {
  const p = event.payload || {};

  if (event.event_type === 'signal') {
    return [
      `🎯 ${safe(event.headline)}`,
      `${safe(p.action)} ${safe(event.pair)} · ${safe(p.bias)} · ${safe(p.grade)}-grade ${safe(p.engine)}`,
      `Entry ${num(p.entry)} · Stop ${num(p.stop)} · Target ${num(p.target)}`,
      `RR ${num(p.rr)} · Risk ${num(p.risk_pct)}`,
      safe(p.reason_short)
    ].join('\n');
  }

  if (event.event_type === 'order') {
    const exitLine = p.exit == null ? '' : ` · Exit ${num(p.exit)}`;
    return [
      `📦 ${safe(event.headline)}`,
      `${safe(p.order_action)} ${safe(event.pair)} · ${safe(p.bias)}`,
      `Entry ${num(p.entry)}${exitLine}`,
      `Stop ${num(p.stop)} · Target ${num(p.target)} · PnL ${num(p.pnl)}`,
      safe(p.reason_short)
    ].join('\n');
  }

  return [
    `🛡️ ${safe(event.headline)}`,
    `${safe(p.status_mode)} · Scope ${safe(p.scope)}`,
    countsLine(p.counts),
    safe(p.reason_short)
  ].filter(Boolean).join('\n');
}

function countsLine(counts) {
  if (!counts || typeof counts !== 'object') return '';
  return `${int(counts.s_grade_count)} S-grade · ${int(counts.a_grade_count)} A-grade · ${int(counts.pairs_active)} active`;
}

async function sendTelegramMessage(env, text) {
  const endpoint = `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`;
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text,
      disable_web_page_preview: true
    })
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw httpError(`telegram send failed: ${data.description || res.status}`, 502);
  }
  return data;
}

async function handleWebhook(update, env) {
  const msg = update?.message?.text?.trim();
  if (!msg) return 'ignored';

  const lower = msg.toLowerCase();
  if (lower === '/start') {
    await sendTelegramMessage(env, 'RTS Sniper Council online. Commands: /health /poll');
    return 'start_sent';
  }
  if (lower === '/health') {
    await sendTelegramMessage(env, 'Telegram worker online.');
    return 'health_sent';
  }
  if (lower === '/poll') {
    const result = await runPoll(env);
    await sendTelegramMessage(env, `Immediate sync complete. Sent ${result.count}. Cursor ${result.cursor || 'none'}.`);
    return 'poll_sent';
  }

  await sendTelegramMessage(env, 'Unknown command. Use /health or /poll.');
  return 'unknown_command';
}

function authorize(request, env) {
  const configured = env.TELEGRAM_ADMIN_SECRET || env.SOURCE_SECRET;
  if (!configured) throw httpError('missing TELEGRAM_ADMIN_SECRET or SOURCE_SECRET in env', 500);
  const supplied = request.headers.get('x-telegram-secret') || request.headers.get('x-source-secret') || request.headers.get('authorization')?.replace(/^Bearer\s+/i, '');
  if (supplied !== configured) throw httpError('unauthorized', 401);
}

function requireEnv(env, keys) {
  for (const key of keys) {
    if (!env[key]) throw httpError(`missing ${key} in env`, 500);
  }
}

function int(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? String(n) : String(v ?? '');
}

function safe(v) {
  return String(v ?? '').trim();
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
