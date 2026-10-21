/** Hooks de datos (TanStack Query). La UI no llama a `http` directamente. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { http, newIdempotencyKey, persistRefreshToken } from './client';
import type {
  AvailabilityDay,
  BookingSummary,
  Hold,
  ProviderDashboard,
  Quote,
  VenueDetail,
  VenueSummary,
} from './types';

export const queryKeys = {
  home: (city?: string) => ['home', city ?? 'default'] as const,
  search: (params: Record<string, unknown>) => ['search', params] as const,
  venue: (id: string) => ['venue', id] as const,
  availability: (id: string, from: string, to: string) => ['availability', id, from, to] as const,
  quote: (id: string, params: Record<string, unknown>) => ['quote', id, params] as const,
  bookings: (role: 'client' | 'provider', status?: string) => ['bookings', role, status ?? 'all'] as const,
  booking: (id: string) => ['booking', id] as const,
  providerDashboard: (period: string) => ['provider-dashboard', period] as const,
};

export interface SearchParams {
  q?: string;
  city?: string;
  lat?: number;
  lng?: number;
  radius_km?: number;
  date_from?: string;
  date_to?: string;
  category?: string[];
  amenities?: string[];
  capacity_min?: number;
  price_max?: number;
  instant_book?: boolean;
  sort?: string;
  limit?: number;
}

export function useHome(city?: string) {
  return useQuery({
    queryKey: queryKeys.home(city),
    queryFn: async () => {
      const { data } = await http.get('/home/featured', { params: { city } });
      return data as Array<{
        section: string;
        title: string;
        subtitle: string | null;
        disclosure: string | null;
        venues: VenueSummary[];
      }>;
    },
    staleTime: 60_000,
  });
}

export function useVenueSearch(params: SearchParams) {
  return useQuery({
    queryKey: queryKeys.search(params),
    queryFn: async () => {
      const { data } = await http.get('/venues', {
        params: { ...params, category: params.category?.join(','), amenities: params.amenities?.join(',') },
      });
      return data as { items: VenueSummary[]; meta: { has_more: boolean; next_cursor: string | null } };
    },
    placeholderData: (previous) => previous,
  });
}

export function useVenue(id: string) {
  return useQuery({
    queryKey: queryKeys.venue(id),
    queryFn: async () => {
      const { data } = await http.get(`/venues/${id}`);
      return data as VenueDetail;
    },
    enabled: Boolean(id),
  });
}

export function useAvailability(id: string, from: string, to: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.availability(id, from, to),
    queryFn: async () => {
      const { data } = await http.get(`/venues/${id}/availability`, { params: { from, to } });
      return data as { venue_id: string; timezone: string; days: AvailabilityDay[] };
    },
    enabled: Boolean(id) && enabled,
    staleTime: 30_000,
  });
}

export function useQuote(
  venueId: string,
  params: { start_at: string; end_at: string; guests: number; extras?: string[] } | null,
) {
  return useQuery({
    queryKey: queryKeys.quote(venueId, params ?? {}),
    queryFn: async () => {
      const { data } = await http.get(`/venues/${venueId}/quote`, {
        params: { start: params!.start_at, end: params!.end_at, guests: params!.guests, extras: params!.extras },
      });
      return data as Quote;
    },
    enabled: Boolean(venueId && params?.start_at && params?.end_at && params?.guests),
    staleTime: 15_000,
  });
}

/** Apartado: idempotente por llave generada una sola vez por intento del usuario. */
export function useCreateHold() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      venue_id: string;
      start_at: string;
      end_at: string;
      guests: number;
      slot_kind?: string;
      extras?: string[];
      quote_total_cents?: number;
    }) => {
      const { data } = await http.post('/bookings/hold', input, {
        headers: { 'Idempotency-Key': newIdempotencyKey() },
      });
      return data as Hold;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bookings'] });
    },
  });
}

export function useExtendHold() {
  return useMutation({
    mutationFn: async (holdId: string) => {
      const { data } = await http.post(`/bookings/holds/${holdId}/extend`);
      return data as Hold;
    },
  });
}

export function useConfirmBooking() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      hold_id: string;
      payment_method: 'card' | 'spei' | 'oxxo' | 'cash_at_venue';
      invoice_requested?: boolean;
      rfc?: string;
    }) => {
      const { data } = await http.post(
        '/bookings',
        { ...input, accept_contract: true },
        { headers: { 'Idempotency-Key': newIdempotencyKey() } },
      );
      return data as BookingSummary;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bookings'] });
    },
  });
}

export function useCreatePaymentIntent() {
  return useMutation({
    mutationFn: async (input: { booking_id: string; kind?: string; method?: string }) => {
      const { data } = await http.post(
        '/payments/intent',
        { kind: 'DEPOSIT', method: 'card', ...input },
        { headers: { 'Idempotency-Key': newIdempotencyKey() } },
      );
      return data as { payment_id: string; client_secret: string | null; requires_action: boolean; amount_cents: number };
    },
  });
}

export function useBookings(role: 'client' | 'provider', status?: string) {
  return useQuery({
    queryKey: queryKeys.bookings(role, status),
    queryFn: async () => {
      const { data } = await http.get('/me/bookings', { params: { role, status } });
      return data as { items: BookingSummary[]; meta: { has_more: boolean } };
    },
  });
}

export function useCancelBooking() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { booking_id: string; reason: string; reason_code: string }) => {
      const { data } = await http.post(
        `/bookings/${input.booking_id}/cancel`,
        {
          reason: input.reason,
          reason_code: input.reason_code,
          request_refund: true,
          acknowledge_policy: true,
        },
        { headers: { 'Idempotency-Key': newIdempotencyKey() } },
      );
      return data as { refund_cents: number; refund_tier_percent: number; policy_explanation: string };
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['bookings'] });
    },
  });
}

export function useProviderDashboard(period: '30d' | '90d' | 'mtd' = '30d') {
  return useQuery({
    queryKey: queryKeys.providerDashboard(period),
    queryFn: async () => {
      const { data } = await http.get('/providers/me/dashboard', { params: { period } });
      return data as ProviderDashboard;
    },
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: async (input: { username: string; password: string }) => {
      const form = new URLSearchParams({ username: input.username, password: input.password });
      const { data } = await http.post('/auth/login', form.toString(), {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });
      await persistRefreshToken(data.refresh_token);
      return data as { access_token: string; refresh_token: string; user: { id: string; role: string } };
    },
  });
}
