/**
 * Cliente de API de la web.
 *
 * Mismo contrato que el backend FastAPI: `{error:{code,message,details,request_id}}`, dinero en
 * centavos, `Idempotency-Key` en las escrituras críticas. Si la API no responde (demo local), cada
 * llamada cae a los datos de demostración en vez de romper la pantalla: la app nunca muestra un
 * callejón sin salida.
 */

export type ApiErrorCode =
  | 'VENUE_NOT_AVAILABLE'
  | 'HOLD_EXPIRED'
  | 'HOLD_LIMIT_REACHED'
  | 'PAYMENT_DUPLICATED'
  | 'CARD_DECLINED'
  | 'PROVIDER_PAYOUTS_NOT_ENABLED'
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

  get isRetryable(): boolean {
    return this.status >= 500 || this.code === 'RATE_LIMITED' || this.code === 'SERVICE_DEGRADED';
  }
}

const BASE = '/api/v1';
const TOKEN_KEY = 'quinta.access_token';

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* almacenamiento no disponible (modo privado): la sesión vive en memoria */
  }
}

export function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, '0'));
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`;
}

/** Marca si la API real está viva; la UI muestra el aviso de modo demostración. */
export let usingDemoData = true;
export const demoListeners = new Set<(demo: boolean) => void>();

function setDemo(value: boolean): void {
  if (usingDemoData === value) return;
  usingDemoData = value;
  demoListeners.forEach((listener) => listener(value));
}

/**
 * Detección de capacidades.
 *
 * En esta máquina conviven varios proyectos y más de uno expone `/api/v1` con el mismo sobre de
 * errores, así que **no basta con comprobar la forma** de la respuesta: hay que confirmar que la
 * API es Quinta. Se acepta si responde con la cabecera `X-Quinta-API: 1` (señal explícita que
 * envía el backend) o si el catálogo trae las categorías propias del producto
 * (`palapa`, `quinta`, `salon`, `terraza`).
 *
 * Si no es Quinta, la web trabaja con el respaldo local y no lanza peticiones condenadas a 404.
 */
const QUINTA_CATEGORY_SLUGS = ['palapa', 'quinta', 'salon', 'terraza'];

let apiProbe: Promise<boolean> | null = null;

export function apiIsQuinta(): Promise<boolean> {
  apiProbe ??= (async () => {
    try {
      const response = await fetch(`${BASE}/categories`, { headers: { Accept: 'application/json' } });
      if (!response.ok) {
        setDemo(true);
        return false;
      }
      if (response.headers.get('x-quinta-api') === '1') {
        setDemo(false);
        return true;
      }
      const data: unknown = await response.json();
      const slugs = Array.isArray(data)
        ? data.map((item) => String((item as { slug?: unknown }).slug ?? '').toLowerCase())
        : [];
      const isQuinta = slugs.some((slug) => QUINTA_CATEGORY_SLUGS.includes(slug));
      setDemo(!isQuinta);
      return isQuinta;
    } catch {
      setDemo(true);
      return false;
    }
  })();
  return apiProbe;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  idempotent?: boolean;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.idempotent) headers['Idempotency-Key'] = idempotencyKey();

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch {
    setDemo(true);
    throw new ApiError({
      code: 'SERVICE_DEGRADED',
      message: 'No pudimos alcanzar el servidor. Estás viendo datos de demostración.',
      status: 0,
    });
  }

  if (response.ok) {
    try {
      const data = (await response.json()) as T;
      setDemo(false);
      return data;
    } catch {
      // El servidor devolvió algo que no es JSON (por ejemplo el HTML de la SPA cuando la API no
      // está detrás de esta ruta). Se trata como «API no disponible» y se usa el respaldo local.
      setDemo(true);
      throw new ApiError({
        code: 'SERVICE_DEGRADED',
        message: 'La API no respondió con JSON en esta ruta.',
        status: 0,
      });
    }
  }

  let payload: { error?: { code?: string; message?: string; details?: Record<string, unknown>; request_id?: string } } = {};
  try {
    payload = (await response.json()) as typeof payload;
  } catch {
    /* respuesta sin cuerpo JSON */
  }

  throw new ApiError({
    code: payload.error?.code ?? 'INTERNAL_ERROR',
    message: payload.error?.message ?? 'Ocurrió un error inesperado.',
    status: response.status,
    details: payload.error?.details,
    requestId: payload.error?.request_id,
  });
}

/**
 * Intenta la API y, si no está disponible, resuelve con datos locales.
 * `fallback` sólo se usa cuando el error es de red o 5xx: un 4xx de negocio se propaga
 * porque la UI debe explicarlo (por ejemplo `VENUE_NOT_AVAILABLE`).
 */
export async function requestOrDemo<T>(
  path: string,
  options: RequestOptions,
  fallback: () => T,
): Promise<T> {
  try {
    return await request<T>(path, options);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 0 || error.isRetryable)) {
      setDemo(true);
      return fallback();
    }
    throw error;
  }
}

export const api = {
  home: () => requestOrDemo('/home/featured', { method: 'GET' }, () => null),
  health: () => request<{ status: string }>('/health'.replace('/api/v1', ''), { method: 'GET' }),
};
