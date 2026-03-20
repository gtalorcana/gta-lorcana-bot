/**
 * GTA Lorcana — ID Check Worker
 *
 * Routes:
 *   GET  /id-check/event?id={event_id}  — fetch event metadata from RPH
 *   POST /id-check/analyze              — run ID safety analysis
 */

const RPH_BASE = 'https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2';

const ALLOWED_ORIGINS = [
  'https://gtalorcana.ca',
  'http://localhost',
  'http://127.0.0.1',
];

// ── Helpers ──────────────────────────────────────────────────────────────────

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.some(o => origin && origin.startsWith(o));
  return {
    'Access-Control-Allow-Origin': allowed ? origin : 'https://gtalorcana.ca',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}

function jsonResponse(data, status = 200, origin = '') {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) },
  });
}

function errResponse(message, status, origin) {
  return jsonResponse({ error: message }, status, origin);
}

async function rphFetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`RPH returned ${res.status} for ${url}`);
  return res.json();
}

// ── Router ───────────────────────────────────────────────────────────────────

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (url.pathname === '/id-check/event' && request.method === 'GET') {
      return handleEvent(url, origin);
    }

    if (url.pathname === '/id-check/analyze' && request.method === 'POST') {
      return handleAnalyze(request, origin);
    }

    return errResponse('Not found', 404, origin);
  },
};

// ── GET /id-check/event?id={event_id} ────────────────────────────────────────

async function handleEvent(url, origin) {
  const eventIdStr = url.searchParams.get('id');
  if (!eventIdStr || !/^\d+$/.test(eventIdStr)) {
    return errResponse('Missing or invalid event ID', 400, origin);
  }
  const eventId = parseInt(eventIdStr, 10);

  // Fetch event from RPH
  let eventData;
  try {
    eventData = await rphFetch(`${RPH_BASE}/events/?id=${eventId}`);
  } catch (e) {
    return errResponse(`RPH API error: ${e.message}`, 502, origin);
  }

  const results = eventData.results ?? [];
  if (results.length === 0) {
    return errResponse('Event not found', 404, origin);
  }
  const event = results[0];

  // Find the Swiss phase
  const phases = event.tournament_phases ?? [];
  const swissPhase = phases.find(p => p.round_type === 'SWISS') ?? phases[0];
  if (!swissPhase) {
    return errResponse('Event has no tournament phases', 400, origin);
  }

  const totalSwissRounds = swissPhase.number_of_rounds;
  const rounds = (swissPhase.rounds ?? []).map(r => ({
    id: r.id,
    round_number: r.round_number,
    status: r.status,
  }));

  // Determine current round (first non-complete, or last round if all done)
  const inProgress = rounds.find(r => r.status !== 'COMPLETE');
  const currentRound = inProgress
    ? inProgress.round_number
    : rounds.length > 0 ? rounds[rounds.length - 1].round_number : 1;

  // Find last completed round
  const completedRounds = rounds.filter(r => r.status === 'COMPLETE');
  if (completedRounds.length === 0) {
    return errResponse('No completed rounds yet — nothing to analyze', 400, origin);
  }
  const lastCompleted = completedRounds[completedRounds.length - 1];

  // Fetch standings for last completed round (used to build player list)
  let standingsData;
  try {
    standingsData = await rphFetch(
      `${RPH_BASE}/tournament-rounds/${lastCompleted.id}/standings`
    );
  } catch (e) {
    return errResponse(`RPH API error fetching standings: ${e.message}`, 502, origin);
  }

  const standings = standingsData.standings ?? [];
  const players = standings
    .map(s => ({
      id: s.player?.id,
      name: s.user_event_status?.best_identifier ?? `Player ${s.player?.id}`,
    }))
    .filter(p => p.id != null)
    .sort((a, b) => a.name.localeCompare(b.name));

  return jsonResponse({
    event_id: eventId,
    event_name: event.name,
    player_count: event.starting_player_count,
    total_swiss_rounds: totalSwissRounds,
    current_round: currentRound,
    rounds,
    players,
  }, 200, origin);
}

// ── POST /id-check/analyze ────────────────────────────────────────────────────

async function handleAnalyze(request, origin) {
  // TODO: implement Simple → Medium → Full analysis
  return errResponse('Not yet implemented', 501, origin);
}
