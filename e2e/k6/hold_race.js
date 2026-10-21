// Prueba de concurrencia: N usuarios intentan apartar el MISMO slot del MISMO venue.
// Objetivo: exactamente un 201 y N-1 respuestas 409 VENUE_NOT_AVAILABLE (nunca dos ganadores).
//
//   k6 run --vus 1 --iterations 1 e2e/k6/hold_race.js

import http from 'k6/http';
import { check, fail } from 'k6';
import { Counter } from 'k6/metrics';

const BASE = __ENV.API_URL || 'http://localhost:8000';
const VENUE_ID = __ENV.VENUE_ID;
const TOKEN = __ENV.TOKEN; // access token de un cliente de prueba
const RACERS = Number(__ENV.RACERS || 25);

const holdCreated = new Counter('hold_created');
const holdConflict = new Counter('hold_conflict');
const unexpected = new Counter('hold_unexpected');

export const options = {
  scenarios: {
    race: {
      executor: 'per-vu-iterations',
      vus: RACERS,
      iterations: 1,
      maxDuration: '40s',
    },
  },
  thresholds: {
    // Un solo ganador: el contador de éxitos no puede pasar de 1
    hold_created: ['count<=1'],
    // El resto debe recibir 409 con el código de negocio
    hold_conflict: [`count>=${RACERS - 1}`],
    hold_unexpected: ['count==0'],
  },
};

function isoSlot() {
  const start = new Date(Date.now() + 21 * 24 * 3600 * 1000);
  start.setUTCHours(15, 0, 0, 0);
  const end = new Date(start.getTime() + 11 * 3600 * 1000);
  return { start_at: start.toISOString(), end_at: end.toISOString() };
}

export default function () {
  if (!VENUE_ID || !TOKEN) fail('Define VENUE_ID y TOKEN');

  const { start_at, end_at } = isoSlot();
  const payload = JSON.stringify({
    venue_id: VENUE_ID,
    start_at,
    end_at,
    guests: 80,
    slot_kind: 'FULL_DAY',
    extras: [],
  });

  const response = http.post(`${BASE}/api/v1/bookings/hold`, payload, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${TOKEN}`,
      'Idempotency-Key': `k6-${__VU}-${Date.now()}`,
    },
    tags: { name: 'POST /bookings/hold' },
  });

  if (response.status === 201) {
    holdCreated.add(1);
    check(response, { '201 trae folio': (r) => Boolean(r.json('folio')) });
  } else if (response.status === 409 && response.json('error.code') === 'VENUE_NOT_AVAILABLE') {
    holdConflict.add(1);
    check(response, {
      '409 trae alternativas': (r) => Array.isArray(r.json('error.details.suggested')),
    });
  } else {
    unexpected.add(1);
    console.error(`respuesta inesperada ${response.status}: ${response.body}`);
  }
}
