/** Tipos del contrato con la API. Reflejan los esquemas Pydantic (fuente de verdad). */

export type BookingState =
  | 'DRAFT'
  | 'HOLD'
  | 'PENDING_PAYMENT'
  | 'CONFIRMED'
  | 'COMPLETED'
  | 'CANCELLED_BY_CLIENT'
  | 'CANCELLED_BY_PROVIDER'
  | 'NO_SHOW'
  | 'REFUNDED'
  | 'DISPUTED';

export type SlotKind = 'FULL_DAY' | 'MORNING' | 'EVENING' | 'NIGHT' | 'CUSTOM';

export interface VenueSummary {
  id: string;
  name: string;
  slug: string;
  headline: string | null;
  city: string;
  state: string;
  neighborhood: string | null;
  capacity_min: number;
  capacity_max: number;
  base_price_cents: number | null;
  currency: string;
  rating_avg: number | null;
  rating_count: number;
  instant_book: boolean;
  status: string;
  cover_url: string | null;
  distance_km?: number | null;
  is_available?: boolean | null;
  price_from_cents?: number | null;
  provider_name?: string | null;
  provider_verified?: boolean;
  sponsored?: boolean;
  badges?: string[];
}

export interface VenueDetail extends VenueSummary {
  description: string | null;
  house_rules: string | null;
  included_items: string[];
  timezone: string;
  min_hours: number;
  max_hours: number;
  buffer_minutes: number;
  slot_kind_default: SlotKind;
  crosses_midnight: boolean;
  curfew_time: string | null;
  deposit_percent: number;
  security_deposit_cents: number | null;
  cleaning_fee_cents: number;
  min_notice_hours: number;
  media: Array<{
    id: string;
    url: string;
    thumbnail_url: string | null;
    alt_text: string | null;
    width: number | null;
    height: number | null;
    sort_order: number;
  }>;
  amenities: Array<{ id: string; slug: string; name: string; is_included_by_default: boolean }>;
  event_types: Array<{ id: string; slug: string; name: string }>;
  is_favorite: boolean;
}

export interface QuoteLine {
  concept: string;
  detail: string | null;
  amount_cents: number;
  is_refundable: boolean;
  is_discount: boolean;
}

export interface Quote {
  venue_id: string;
  start_at: string;
  end_at: string;
  venue_timezone: string;
  duration_hours: number;
  guests: number;
  lines: QuoteLine[];
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
  hold_ttl_seconds: number;
  warnings: string[];
  snapshot: Record<string, unknown>;
  expires_at: string;
}

export interface Hold {
  id: string;
  folio: string;
  venue_id: string;
  status: 'ACTIVE' | 'CONVERTED' | 'EXPIRED' | 'RELEASED';
  start_at: string;
  end_at: string;
  venue_timezone: string;
  guests: number;
  slot_kind: SlotKind;
  total_cents: number;
  deposit_cents: number;
  currency: string;
  expires_at: string;
  seconds_remaining: number;
  can_extend: boolean;
  quote_snapshot: Record<string, unknown>;
  venue_name: string | null;
  venue_cover_url: string | null;
  quote?: Quote;
  next_step?: 'payment';
}

export interface BookingSummary {
  id: string;
  folio: string;
  state: BookingState;
  venue_id: string;
  venue_name: string | null;
  venue_cover_url: string | null;
  city: string | null;
  start_at: string;
  end_at: string;
  venue_timezone: string;
  guests: number;
  total_cents: number;
  paid_cents: number;
  balance_cents: number;
  currency: string;
  can_cancel: boolean;
  can_reschedule: boolean;
  can_review: boolean;
  can_pay_balance: boolean;
  next_action: 'pay_deposit' | 'pay_balance' | 'check_in' | 'review' | 'none';
  provider_name: string | null;
}

export interface AvailabilityDay {
  date: string;
  is_available: boolean;
  reason_unavailable: string | null;
  slots: Array<{
    start_at: string;
    end_at: string;
    slot_kind: SlotKind;
    price_cents: number;
    currency: string;
    is_available: boolean;
  }>;
}

export interface ProviderDashboard {
  provider_id: string;
  period: string;
  occupancy_rate: number;
  revenue_cents: number;
  revenue_currency: string;
  confirmed_bookings: number;
  pending_requests: number;
  upcoming_events: number;
  cancelled_by_client: number;
  cancelled_by_provider: number;
  avg_ticket_cents: number;
  rating_avg: number | null;
  response_time_minutes: number | null;
  next_payout_cents: number | null;
  next_payout_date: string | null;
  pending_tasks: Array<{ kind: string; label: string; severity: string }>;
}

export interface Money {
  amount_cents: number;
  currency: string;
}

export function formatMoney(cents: number, currency = 'MXN', locale = 'es-MX'): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

export function formatCountdown(seconds: number): string {
  const clamped = Math.max(seconds, 0);
  const minutes = Math.floor(clamped / 60);
  const rest = clamped % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}
