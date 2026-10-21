/**
 * Cliente HTTP: refresco de tokens, idempotencia y manejo de códigos de error estables.
 *
 * Reglas:
 * - El `access_token` vive en memoria + SecureStore; el refresh se rota en cada uso.
 * - Toda escritura crítica manda `Idempotency-Key` (uuid v4 generado por operación).
 * - El error del backend (`{error:{code,message,details}}`) se convierte en `ApiError` tipado:
 *   la UI decide por `code`, nunca por el texto.
 */

import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios';
import * as SecureStore from 'expo-secure-store';
import Constants from 'expo-constants';

import { sessionStore } from '../store/session';

export const API_URL =
  (Constants.expoConfig?.extra?.apiUrl as string | undefined) ?? 'http://localhost:8000';

export type ApiErrorCode =
  | 'VENUE_NOT_AVAILABLE'
  | 'HOLD_EXPIRED'
  | 'HOLD_LIMIT_REACHED'
  | 'PAYMENT_DUPLICATED'
  | 'CARD_DECLINED'
  | 'PROVIDER_PAYOUTS_NOT_ENABLED'
  | 'REVIEW_NOT_ALLOWED'
  | 'REVIEW_WINDOW_CLOSED'
  | 'CONTACT_SHARING_FLAGGED'
  | 'RATE_LIMITED'
  | 'SERVICE_DEGRADED'
  | 'VALIDATION_ERROR'
  | 'UNAUTHORIZED'
  | 'FORBIDDEN'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'INTERNAL_ERROR'
  | (string & {});

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId?: string;

  constructor(params: {
    code: ApiErrorCode;
    message: string;
    status: number;
    details?: Record<string, unknown>;
    requestId?: string;
  }) {
    super(params.message);
    this.name = 'ApiError';
    this.code = params.code;
    this.status = params.status;
    this.details = params.details ?? {};
    this.requestId = params.requestId;
  }

  /** Mensaje ya listo para mostrar; los códigos conocidos tienen copy propio en la UI. */
  get isRetryable(): boolean {
    return this.status >= 500 || this.code === 'RATE_LIMITED' || this.code === 'SERVICE_DEGRADED';
  }
}

const REFRESH_KEY = 'quinta.refresh_token';

export function newIdempotencyKey(): string {
  // RFC 4122 v4 sin dependencias: suficiente para llaves de idempotencia
  const bytes = Array.from({ length: 16 }, () => Math.floor(Math.random() * 256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.map((b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex
    .slice(8, 10)
    .join('')}-${hex.slice(10, 16).join('')}`;
}

export const http: AxiosInstance = axios.create({
  baseURL: `${API_URL}/api/v1`,
  timeout: 15000,
  headers: { 'Content-Type': 'application/json' },
});

http.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  const token = sessionStore.getState().accessToken;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  if (__DEV__) {
    config.headers['X-Request-ID'] = newIdempotencyKey();
  }
  return config;
});

let refreshPromise: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = await SecureStore.getItemAsync(REFRESH_KEY);
  if (!refreshToken) return null;
  try {
    const { data } = await axios.post(`${API_URL}/api/v1/auth/refresh`, {
      refresh_token: refreshToken,
    });
    await SecureStore.setItemAsync(REFRESH_KEY, data.refresh_token);
    sessionStore.getState().setSession(data.access_token, data.refresh_token, data.user ?? null);
    return data.access_token as string;
  } catch {
    await SecureStore.deleteItemAsync(REFRESH_KEY);
    sessionStore.getState().clear();
    return null;
  }
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const status = error.response?.status ?? 0;
    const original = error.config as InternalAxiosRequestConfig & { _retried?: boolean };

    if (status === 401 && original && !original._retried && !original.url?.includes('/auth/')) {
      original._retried = true;
      refreshPromise = refreshPromise ?? refreshAccessToken();
      const token = await refreshPromise;
      refreshPromise = null;
      if (token) {
        original.headers.Authorization = `Bearer ${token}`;
        return http.request(original);
      }
    }

    const payload = error.response?.data as
      | { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } }
      | undefined;

    if (payload?.error?.code) {
      throw new ApiError({
        code: payload.error.code,
        message: payload.error.message ?? 'Ocurrió un error inesperado.',
        status,
        details: payload.error.details,
        requestId: payload.error.request_id,
      });
    }

    if (status === 0) {
      throw new ApiError({
        code: 'SERVICE_DEGRADED',
        message: 'Sin conexión. Revisa tu red; tu apartado sigue activo en el servidor.',
        status: 0,
      });
    }
    throw new ApiError({
      code: 'INTERNAL_ERROR',
      message: 'Algo salió mal de nuestro lado. Intenta de nuevo.',
      status,
    });
  },
);

export async function persistRefreshToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(REFRESH_KEY, token);
}
