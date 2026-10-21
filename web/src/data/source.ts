/**
 * Capa de datos: **primero la API, después el respaldo local**.
 *
 * Cada función intenta el backend de Quinta (`/api/v1`, proxied a la API real) y, si no está
 * disponible, resuelve con el dataset de demostración conservando la misma forma de dato. Así la
 * web sirve para dos cosas: revisar el diseño sin infraestructura y funcionar contra la API real
 * en cuanto esté arriba.
 *
 * Los mapeos viven aquí y no en las pantallas: el contrato de la API (snake_case, centavos) se
 * traduce a los tipos de la interfaz en un solo lugar.
 */

import { ApiError, apiIsQuinta, request } from '../api/client';
import {
  VENUES,
  VENUES_BY_ID,
  availabilityFor,
  buildQuote,
  demoBookings,
  demoProviderDashboard,
  type BookingDemo,
  type Quote,
  type Venue,
} from './demo';

export interface Result<T> {
  data: T;
  fromFallback: boolean;
}

/** Intenta la API y cae al respaldo si no está disponible.

Comprobaciones, en orden:
1. ¿Detrás de `/api/v1` está la API de Quinta? Si no (puerto ocupado por otro proyecto, 404/401),
   se responde con el respaldo local sin lanzar la petición.
2. Si lo está y la llamada falla por red o 5xx, también se cae al respaldo.
3. Un 4xx de negocio (409 de disponibilidad, 410 de apartado vencido) **se propaga**: la UI debe
   explicarlo, no esconderlo.
*/
async function apiFirst<T>(load: () => Promise<T>, fallback: () => T): Promise<Result<T>> {
  if (!(await apiIsQuinta())) {
    return { data: fallback(), fromFallback: true };
  }
  try {
    const data = await load();
    return { data, fromFallback: false };
  } catch (error) {
    if (error instanceof ApiError && (error.status === 0 || error.isRetryable)) {
      return { data: fallback(), fromFallback: true };
    }
    throw error;
  }
}

// ── Mapeos del contrato de la API ─────────────────────────────────────────────

interface ApiVenue {
  id: string;
  name: string;
  headline: string | null;
  city: string;
  state: string;
  capacity_min: number;
  capacity_max: number;
  base_price_cents: number | null;
  currency: string;
  rating_avg: number | null;
  rating_count: number;
  instant_book: boolean;
  cover_url: string | null;
  sponsored?: boolean;
  is_available?: boolean | null;
  distance_km?: number | null;
}

/** La API no devuelve la categoría ni las amenidades en el listado: se completan desde el local. */
function toVenue(api: ApiVenue): Venue {
  const local = VENUES_BY_ID.get(api.id);
  if (local) {
    return {
      ...local,
      name: api.name,
      headline: api.headline ?? local.headline,
      capacityMin: api.capacity_min,
      capacityMax: api.capacity_max,
      basePriceCents: api.base_price_cents ?? local.basePriceCents,
      rating: api.rating_avg ?? local.rating,
      reviews: api.rating_count,
      instantBook: api.instant_book,
      sponsored: Boolean(api.sponsored),
      availableOnDate: api.is_available ?? null,
      images: api.cover_url ? [{ src: api.cover_url, alt: `Foto de ${api.name}` }, ...local.images] : local.images,
    };
  }
  // Venue que existe en la API pero no en la demo: se muestra con lo que la API sí entrega.
  return {
    id: api.id,
    name: api.name,
    category: 'Salón',
    city: api.city,
    state: api.state,
    headline: api.headline ?? '',
    description: '',
    capacityMin: api.capacity_min,
    capacityMax: api.capacity_max,
    minHours: 4,
    bufferMinutes: 60,
    basePriceCents: api.base_price_cents ?? 0,
    extraGuestFrom: api.capacity_min,
    extraGuestCents: 0,
    cleaningCents: 0,
    securityDepositCents: 0,
    depositPercent: 30,
    minNoticeHours: 24,
    instantBook: api.instant_book,
    sponsored: Boolean(api.sponsored),
    rating: api.rating_avg ?? 0,
    reviews: api.rating_count,
    eventTypes: [],
    includes: [],
    amenities: [],
    houseRules: '',
    images: api.cover_url ? [{ src: api.cover_url, alt: `Foto de ${api.name}` }] : [],
    bookedOffsets: [],
    availableOnDate: api.is_available ?? null,
  };
}

interface ApiQuote {
  lines: Array<{ concept: string; detail: string | null; amount_cents: number; is_discount: boolean; is_refundable: boolean }>;
  subtotal_cents: number;
  extras_cents: number;
  cleaning_cents: number;
  discount_cents: number;
  service_fee_cents: number;
  tax_cents: number;
  security_deposit_cents: number;
  total_cents: number;
  deposit_cents: number;
  currency: string;
  duration_hours: number;
  warnings: string[];
}

function toQuote(api: ApiQuote, venueId: string, guests: number): Quote {
  return {
    venueId,
    hours: api.duration_hours,
    guests,
    lines: api.lines.map((line) => ({
      concept: line.concept,
      detail: line.detail ?? undefined,
      amountCents: line.amount_cents,
      isDiscount: line.is_discount,
      isRefundable: line.is_refundable,
    })),
    subtotalCents: api.subtotal_cents,
    extrasCents: api.extras_cents,
    cleaningCents: api.cleaning_cents,
    discountCents: api.discount_cents,
    serviceFeeCents: api.service_fee_cents,
    taxCents: api.tax_cents,
    securityDepositCents: api.security_deposit_cents,
    totalCents: api.total_cents,
    depositCents: api.deposit_cents,
    currency: api.currency,
    warnings: api.warnings,
  };
}

interface ApiBooking {
  id: string;
  folio: string;
  state: BookingDemo['state'];
  venue_id: string;
  venue_name: string | null;
  start_at: string;
  end_at: string;
  guests: number;
  total_cents: number;
  paid_cents: number;
  can_review: boolean;
}

// ── Catálogo ─────────────────────────────────────────────────────────────────

export interface SearchInput {
  dateFrom: string;
  dateTo: string;
  guests: number;
  category?: string;
  query?: string;
}

export async function searchVenues(input: SearchInput): Promise<Result<{ venues: Venue[]; fromApi: boolean }>> {
  const params = new URLSearchParams({
    date_from: input.dateFrom,
    date_to: input.dateTo,
    capacity_min: String(input.guests),
    limit: '24',
    sort: 'recommended',
  });
  if (input.query) params.set('q', input.query);

  const result = await apiFirst(
    async () => {
      const response = await request<{ items: ApiVenue[] }>(`/venues?${params.toString()}`);
      return response.items.map(toVenue);
    },
    () => VENUES,
  );
  return { data: { venues: result.data, fromApi: !result.fromFallback }, fromFallback: result.fromFallback };
}

export async function getVenue(venueId: string): Promise<Result<Venue>> {
  return apiFirst(
    async () => toVenue(await request<ApiVenue>(`/venues/${venueId}`)),
    () => {
      const local = VENUES_BY_ID.get(venueId);
      if (!local) throw new ApiError({ code: 'NOT_FOUND', message: 'Espacio no encontrado.', status: 404 });
      return local;
    },
  );
}

export interface DayAvailability {
  date: string;
  isAvailable: boolean;
  reason: string | null;
}

export async function getAvailability(
  venue: Venue,
  from: Date,
  days: number,
): Promise<Result<DayAvailability[]>> {
  const fromIso = from.toISOString().slice(0, 10);
  const toIso = new Date(from.getTime() + (days - 1) * 86400000).toISOString().slice(0, 10);

  return apiFirst(
    async () => {
      const response = await request<{
        days: Array<{ date: string; is_available: boolean; reason_unavailable: string | null }>;
      }>(`/venues/${venue.id}/availability?from=${fromIso}&to=${toIso}`);
      return response.days.map((day) => ({
        date: day.date,
        isAvailable: day.is_available,
        reason: day.reason_unavailable,
      }));
    },
    () =>
      availabilityFor(venue, from, days).map((day) => ({
        date: day.date.toISOString().slice(0, 10),
        isAvailable: day.isAvailable,
        reason: day.reason,
      })),
  );
}

export async function getQuote(
  venue: Venue,
  startAt: Date,
  endAt: Date,
  guests: number,
  couponCode?: string,
): Promise<Result<Quote>> {
  const params = new URLSearchParams({
    start: startAt.toISOString(),
    end: endAt.toISOString(),
    guests: String(guests),
  });
  if (couponCode) params.set('coupon_code', couponCode);

  return apiFirst(
    async () => toQuote(await request<ApiQuote>(`/venues/${venue.id}/quote?${params.toString()}`), venue.id, guests),
    () => buildQuote({ venue, startAt, endAt, guests }),
  );
}

// ── Reservas ─────────────────────────────────────────────────────────────────

export async function listMyBookings(): Promise<Result<BookingDemo[]>> {
  return apiFirst(
    async () => {
      const response = await request<{ items: ApiBooking[] }>('/me/bookings?role=client');
      return response.items.map((booking) => ({
        id: booking.id,
        folio: booking.folio,
        venueId: booking.venue_id,
        state: booking.state,
        startAt: booking.start_at,
        endAt: booking.end_at,
        guests: booking.guests,
        totalCents: booking.total_cents,
        paidCents: booking.paid_cents,
        canReview: booking.can_review,
      }));
    },
    () => demoBookings(),
  );
}

export interface HoldResult {
  id: string;
  folio: string;
  expiresAt: string;
  secondsRemaining: number;
}

/**
 * Apartado real: idempotente por `Idempotency-Key`. Si la API no responde, se devuelve un folio
 * local para que la demo siga siendo navegable (el aviso de modo demostración lo deja claro).
 */
export async function createHold(input: {
  venueId: string;
  startAt: Date;
  endAt: Date;
  guests: number;
  quoteTotalCents?: number;
}): Promise<Result<HoldResult>> {
  return apiFirst(
    async () => {
      const hold = await request<{ id: string; folio: string; expires_at: string; seconds_remaining: number }>(
        '/bookings/hold',
        {
          method: 'POST',
          idempotent: true,
          body: {
            venue_id: input.venueId,
            start_at: input.startAt.toISOString(),
            end_at: input.endAt.toISOString(),
            guests: input.guests,
            slot_kind: 'FULL_DAY',
            extras: [],
            quote_total_cents: input.quoteTotalCents,
          },
        },
      );
      return {
        id: hold.id,
        folio: hold.folio,
        expiresAt: hold.expires_at,
        secondsRemaining: hold.seconds_remaining,
      };
    },
    () => {
      const expires = new Date(Date.now() + 15 * 60_000);
      return {
        id: `demo-${Date.now()}`,
        folio: `HD-${Math.random().toString(36).slice(2, 8).toUpperCase()}`,
        expiresAt: expires.toISOString(),
        secondsRemaining: 15 * 60,
      };
    },
  );
}

export async function confirmBooking(input: {
  holdId: string;
  method: 'card' | 'spei' | 'cash';
  rfc?: string;
}): Promise<Result<{ folio: string }>> {
  return apiFirst(
    async () => {
      const booking = await request<{ folio: string }>('/bookings', {
        method: 'POST',
        idempotent: true,
        body: {
          hold_id: input.holdId,
          payment_method: input.method === 'cash' ? 'cash_at_venue' : input.method,
          accept_contract: true,
          invoice_requested: Boolean(input.rfc),
          rfc: input.rfc,
        },
      });
      return { folio: booking.folio };
    },
    () => ({ folio: `QX-${Math.random().toString(36).slice(2, 8).toUpperCase()}` }),
  );
}

// ── Panel del anfitrión ──────────────────────────────────────────────────────

export async function getProviderDashboard(): Promise<Result<ReturnType<typeof demoProviderDashboard>>> {
  return apiFirst(
    async () => {
      const api = await request<{
        occupancy_rate: number;
        revenue_cents: number;
        confirmed_bookings: number;
        upcoming_events: number;
        avg_ticket_cents: number;
        rating_avg: number | null;
        next_payout_cents: number | null;
        cancelled_by_client: number;
        cancelled_by_provider: number;
        pending_tasks: Array<{ kind: string; label: string; severity: string }>;
      }>('/providers/me/dashboard?period=30d');

      const local = demoProviderDashboard();
      return {
        ...local,
        occupancyRate: api.occupancy_rate,
        revenueCents: api.revenue_cents,
        confirmedBookings: api.confirmed_bookings,
        upcomingEvents: api.upcoming_events,
        avgTicketCents: api.avg_ticket_cents,
        rating: api.rating_avg ?? local.rating,
        cancellations: api.cancelled_by_client + api.cancelled_by_provider,
        nextPayoutCents: api.next_payout_cents ?? local.nextPayoutCents,
        pendingTasks: api.pending_tasks.map((task) => ({
          kind: task.kind as 'payouts' | 'verification' | 'requests',
          label: task.label,
          severity: task.severity as 'blocking' | 'action' | 'info',
        })),
      };
    },
    () => demoProviderDashboard(),
  );
}
