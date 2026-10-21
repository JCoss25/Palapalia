# 03 · API: endpoints, códigos de error y ejemplos

OpenAPI 3.1 se genera solo: `GET /openapi.json` · Swagger UI en `/docs` · ReDoc en `/redoc`.
La especificación vive en los esquemas Pydantic v2 (`app/schemas/*`) y en `response_model` de cada
ruta; este documento es el índice navegable con las decisiones que no caben en el esquema.

## 3.1 Convenciones

| Tema | Regla |
|---|---|
| Prefijo | `/api/v1` (`settings.api_v1_prefix`) |
| Auth | `Authorization: Bearer <access>`; login por `application/x-www-form-urlencoded` (OAuth2) |
| Idempotencia | `Idempotency-Key` obligatorio en `POST /bookings/hold`, `POST /bookings`, `POST /payments/intent`, `POST /refunds`, `POST /admin/payouts/{id}/release`, `POST /admin/impersonate` |
| Errores | `{"error": {"code", "message", "details", "request_id"}}` |
| Dinero | enteros en centavos (`*_cents`) + `currency` ISO-4217 |
| Tiempo | ISO-8601 con offset obligatorio en request; respuesta siempre UTC + `venue_timezone` |
| Paginación | `limit` + `cursor` opaco; `meta.next_cursor` |
| Tasa de límite | cabeceras `X-RateLimit-*`, `Retry-After` en 429 |
| Trazabilidad | `X-Request-ID` en respuesta y logs |

## 3.2 Catálogo de endpoints

### Auth (`app/api/v1/auth.py`)

| Método | Ruta | Auth/RBAC | Body / params | 2xx | 4xx / 5xx |
|---|---|---|---|---|---|
| POST | `/auth/register` | público | `RegisterRequest` | 201 `LoginResponse` | 403 `UNDERAGE_NOT_ALLOWED` · 409 `EMAIL_ALREADY_REGISTERED`, `PHONE_ALREADY_REGISTERED` · 422 `VALIDATION_ERROR`, `TOS_NOT_ACCEPTED` |
| POST | `/auth/login` | público (form) | `OAuth2PasswordRequestForm` | 200 `LoginResponse` | 401 `INVALID_CREDENTIALS` · 403 `ACCOUNT_SUSPENDED` · 429 `RATE_LIMITED` |
| POST | `/auth/refresh` | público | `RefreshRequest` | 200 `TokenPair` | 401 `REFRESH_TOKEN_INVALID`, `REFRESH_TOKEN_REUSED` |
| POST | `/auth/logout` | cliente | `LogoutRequest` | 204 | 401 |
| POST | `/auth/forgot-password` | público | `ForgotPasswordRequest` | 200 `OtpResponse` | 429 |
| POST | `/auth/reset-password` | público | `ResetPasswordRequest` | 204 | 400 `PASSWORD_RESET_TOKEN_INVALID` |
| POST | `/auth/verify-email` | público (token) | `VerifyEmailRequest` | 200 `UserOut` | 401 |
| POST | `/auth/verify-phone` | público (OTP) | `VerifyOtpRequest` | 200 `UserOut` | 400 `OTP_INVALID` · 410 `OTP_EXPIRED` · 429 `OTP_TOO_MANY_ATTEMPTS` |
| POST | `/auth/otp/resend` | cliente | `RequestOtpRequest` | 200 `OtpResponse` | 429 |
| GET | `/me` | cliente | — | 200 `UserOut` | 401 |
| PATCH | `/me` | cliente | `UserUpdateRequest` | 200 `UserOut` | 409 `PHONE_ALREADY_REGISTERED` |
| DELETE | `/me` | cliente | `DeleteAccountRequest` | 200 `DeleteAccountResponse` | 409 `CONFLICT` (reservas futuras) |
| GET | `/me/export` | cliente | — | 202 | 401 |
| GET | `/me/favorites` | cliente | `limit`, `cursor` | 200 `Paginated[VenueSummaryOut]` | 401 |
| GET | `/me/bookings` | cliente/proveedor | `role`, `status[]`, `from_date`, `to_date` | 200 `Paginated[BookingSummaryOut]` | 403 |
| GET | `/me/bookings/upcoming` | cliente | — | 200 `list[BookingSummaryOut]` | 401 |
| GET | `/admin/users` | ADMIN | `q`, `role`, `status` | 200 `Paginated[AdminUserOut]` | 403 |
| PATCH | `/admin/users/{id}` | ADMIN | `AdminUserPatch` | 200 `AdminUserOut` | 403 · 404 |
| POST | `/admin/users/{id}/ban` | ADMIN | `BanUserRequest` | 200 `AdminUserOut` | 403 · 404 |
| POST | `/admin/users/{id}/role` | ADMIN | `RoleGrantRequest` | 200 `AdminUserOut` | 403 |

### Proveedores (`providers.py`)

| Método | Ruta | Auth | Body | 2xx | Errores |
|---|---|---|---|---|---|
| POST | `/providers/apply` | cliente | `ProviderApplyRequest` | 201 `ProviderOut` | 409 `VERIFICATION_ALREADY_PENDING` · 422 `TOS_NOT_ACCEPTED` |
| GET | `/providers/me` | proveedor | — | 200 `ProviderOut` | 404 |
| PATCH | `/providers/me` | proveedor | `ProviderPatchRequest` | 200 `ProviderOut` | 409 `PROVIDER_PAYOUTS_NOT_ENABLED` (instant book sin cobros) |
| GET | `/providers/me/verification-status` | proveedor | — | 200 `VerificationStatusOut` | 404 |
| POST | `/providers/me/verification-documents` | proveedor | `VerificationDocumentCreate` | 201 `VerificationDocumentOut` | 422 `VERIFICATION_DOCUMENT_INVALID` |
| GET | `/providers/me/dashboard` | proveedor aprobado | `period` | 200 `ProviderDashboardOut` | 403 `PROVIDER_NOT_APPROVED` |
| GET | `/providers/{id}` | público | — | 200 `ProviderPublicOut` | 404 |
| GET | `/providers/{id}/rating-summary` | público | — | 200 `dict` | 404 |
| GET | `/admin/providers` | ADMIN | `status` | 200 `Paginated[ProviderOut]` | 403 |
| POST | `/admin/providers/{id}/approve` | ADMIN | `ProviderApproveRequest` | 200 `ProviderOut` | 403 · 404 |

### Venues (`venues.py`)

| Método | Ruta | Auth | Notas | Errores clave |
|---|---|---|---|---|
| POST | `/venues` | proveedor aprobado | Geocodifica si falta el punto | 422 `ADDRESS_NOT_GEOCODABLE` · 409 `VENUE_DUPLICATE` |
| GET | `/venues` | público | 21 filtros + `sort`; `is_available` sólo con fecha | 422 radio > 120 km |
| GET | `/venues/{id}` | público | incrementa `views_count` | 404 |
| PATCH | `/venues/{id}` | dueño | No altera reservas confirmadas (snapshot) | 403 · 409 `PROVIDER_PAYOUTS_NOT_ENABLED` |
| DELETE | `/venues/{id}` | dueño | Soft-delete; bloquea si hay reservas futuras | 409 `CONFLICT` |
| POST | `/venues/{id}/media` | dueño | Devuelve URL pre-firmada; el registro nace en moderación `PENDING` | 415 `MEDIA_TYPE_NOT_ALLOWED` · 409 `MEDIA_LIMIT_REACHED` |
| PATCH | `/venues/{id}/media/reorder` | dueño | Orden + portada | 422 |
| DELETE | `/venues/{id}/media/{mediaId}` | dueño | Borra el objeto del bucket | 404 |
| POST/DELETE | `/venues/{id}/favorite` | cliente | Idempotente | 404 |
| POST | `/admin/venues/{id}/suspend` | ADMIN | Cancela futuras con reembolso total si se pide | 403 · 404 |

**Parámetros de `GET /venues`**: `q, category[], city, state, lat, lng, radius_km, capacity_min,
capacity_max, price_min, price_max, date_from, date_to, time_slot, amenities[], event_types[],
rating_min, instant_book, verified_only, sort, page, limit, cursor`.

### Disponibilidad y precios (`availability.py`)

| Método | Ruta | Auth | Errores clave |
|---|---|---|---|
| GET | `/venues/{id}/availability-rules` | dueño | 403 |
| PUT | `/venues/{id}/availability-rules` | dueño | 422 `AVAILABILITY_RULE_CONFLICT` (bloques solapados del mismo tipo) |
| GET | `/venues/{id}/availability?from&to&slot&guests` | público | 422 rango > 120 días |
| POST | `/venues/{id}/blackout-dates` | dueño | 409 `BLACKOUT_OVERLAPS_CONFIRMED_BOOKING` (salvo `force=true`) |
| DELETE | `/venues/{id}/blackout-dates/{id}` | dueño | 404 |
| GET | `/venues/{id}/pricing-rules` | dueño | 403 |
| PUT | `/venues/{id}/pricing-rules` | dueño | 422 `NO_PRICING_RULE` (sin tarifa BASE) |
| GET | `/venues/{id}/quote?start&end&guests&extras[]` | opcional | 422 `GUESTS_OUT_OF_RANGE`, `MIN_HOURS_NOT_MET`, `DATE_IN_PAST`, `INVALID_DURATION`, `NO_PRICING_RULE`, `TIMEZONE_INVALID` |

### Reservas (`bookings.py`)

| Método | Ruta | Auth | Idempotencia | Errores clave |
|---|---|---|---|---|
| POST | `/bookings/hold` | cliente | sí | 409 `VENUE_NOT_AVAILABLE` (con `suggested`), `QUOTE_EXPIRED` · 410 `HOLD_EXPIRED` · 429 `HOLD_LIMIT_REACHED` |
| GET | `/bookings/holds/{id}` | cliente/admin | — | 410 `HOLD_EXPIRED` |
| POST | `/bookings/holds/{id}/extend` | cliente | — | 410 (una sola extensión) |
| POST | `/bookings/holds/{id}/release` | cliente | — | 204 |
| POST | `/bookings` | cliente | sí | 410 `HOLD_EXPIRED` · 409 `PAYMENT_DUPLICATED`, `PROVIDER_PAYOUTS_NOT_ENABLED` · 400 `TOS_NOT_ACCEPTED` |
| GET | `/bookings/{id}` | partes/admin | — | 403 · 404 |
| GET | `/bookings/{id}/contract` | partes | — | 409 `CONTRACT_NOT_READY` |
| GET | `/bookings/{id}/refund-preview` | partes | — | 404 |
| POST | `/bookings/{id}/cancel` | cliente/proveedor/admin | sí | 409 `INVALID_STATE_TRANSITION`, `REFUND_NOT_ALLOWED` |
| POST | `/bookings/{id}/reschedule` | partes | — | 409 `RESCHEDULE_TARGET_UNAVAILABLE` |
| POST | `/bookings/{id}/check-in` | partes | — | 409 `CHECK_IN_NOT_ALLOWED_YET` · 400 `QR_TOKEN_INVALID` |
| POST | `/bookings/{id}/check-out` | proveedor | — | 409 falta check-in · 409 `EVIDENCE_LIMIT_REACHED` (daños sin foto) |
| POST | `/bookings/{id}/complete` | proveedor | — | 403 |
| POST | `/bookings/{id}/no-show` | proveedor | — | 422 sin evidencia · 409 `BOOKING_ALREADY_STARTED` |
| POST | `/bookings/{id}/dispute` | partes | — | 409 `DISPUTE_ALREADY_OPEN` |
| PATCH | `/admin/bookings/{id}/resolve` | ADMIN | — | 404 `DISPUTE_NOT_FOUND` · 409 `DISPUTE_DEADLINE_PASSED` |

### Pagos (`payments.py`)

| Método | Ruta | Auth | Errores clave |
|---|---|---|---|
| POST | `/payments/intent` | cliente | 402 `CARD_DECLINED`, `PAYMENT_FAILED`, `PAYMENT_REQUIRES_ACTION` · 409 `PAYMENT_DUPLICATED` · 422 `AMOUNT_MISMATCH`, `CURRENCY_MISMATCH` |
| POST | `/payments/webhook` | firma HMAC | 400 `WEBHOOK_SIGNATURE_INVALID`; 200 siempre si el evento ya estaba aplicado |
| GET | `/payments/{id}` | partes | 403 · 404 |
| GET | `/me/payments` | cliente | 401 |
| POST | `/refunds` | cliente/soporte | 409 `REFUND_NOT_ALLOWED`, `REFUND_ALREADY_EXISTS` · 422 `REFUND_EXCEEDS_PAYMENT` |
| GET | `/refunds/{id}` | partes | 403 |
| GET | `/providers/me/payouts` | proveedor | 404 |
| GET | `/admin/payouts` · POST `/admin/payouts/{id}/release` | ADMIN | 409 `PAYOUT_FROZEN` |
| POST | `/invoices` · GET `/invoices/{id}` | cliente | 403 · 404 |

### Reseñas, mensajería, notificaciones, catálogo, cupones, admin y webhooks

| Método | Ruta | Auth | Errores clave |
|---|---|---|---|
| POST | `/reviews` | cliente con booking `COMPLETED` | 403 `REVIEW_NOT_ALLOWED` · 409 `REVIEW_ALREADY_EXISTS` · 410 `REVIEW_WINDOW_CLOSED` |
| PATCH/DELETE | `/reviews/{id}` | autor | 410 (ventana de 7 días) |
| POST | `/reviews/{id}/reply` | proveedor | 422 `MESSAGE_BLOCKED_BY_MODERATION` (teléfonos en respuesta pública) |
| POST | `/reviews/{id}/report` | cliente | 202 |
| GET | `/venues/{id}/reviews` · `/venues/{id}/rating-summary` | público | — |
| PATCH | `/admin/reviews/{id}` | ADMIN/SUPPORT | 404 |
| POST | `/threads` · GET `/threads` | partes | 404 `VENUE_NOT_FOUND` |
| GET/POST | `/threads/{id}/messages` | partes | 422 `MESSAGE_BLOCKED_BY_MODERATION` (hilo limitado) |
| PATCH | `/messages/{id}/read` | partes | 404 |
| WS | `/ws/threads/{id}?token=` | partes | cierre 1008 si no autorizado |
| GET | `/notifications` · PATCH `/{id}/read` · `/read-all` | cliente | 404 |
| GET/PUT | `/me/notification-preferences` | cliente | 422 |
| POST/DELETE | `/me/push-devices` | cliente | 422 |
| GET | `/categories`, `/amenities`, `/event-types` | público | — |
| GET | `/search/suggestions?q=` | público | 422 `q` < 2 |
| GET | `/home/featured` | público | — |
| POST | `/coupons/validate` | cliente | devuelve `valid=false` con `reason` en vez de 4xx |
| GET/POST/PATCH/DELETE | `/admin/coupons` | ADMIN | 409 `CONFLICT` · 404 `COUPON_NOT_FOUND` |
| POST | `/admin/categories`, `/admin/amenities`, `/admin/event-types` | ADMIN | 403 |
| GET | `/admin/metrics`, `/admin/audit-logs`, `/admin/audit-logs/verify`, `/admin/disputes`, `/admin/venue-reports` | ADMIN/SUPPORT | 403 |
| POST | `/admin/venues/merge` | ADMIN | 404 |
| POST | `/admin/impersonate` | ADMIN | 403 (no se impersona a otro admin) |
| GET | `/admin/webhooks/events` · POST `/{id}/replay` · GET `/admin/webhooks/deliveries` | ADMIN | 404 |
| POST/GET/DELETE | `/webhooks/subscriptions` · GET `/webhooks/deliveries` | proveedor | 409 (URL duplicada, máx. 5) |

## 3.3 Ejemplos

### Apartar (éxito)

```http
POST /api/v1/bookings/hold
Authorization: Bearer <access>
Idempotency-Key: 4f0b7f1e-9a4e-4c3f-9b5a-1e2d3c4b5a69
Content-Type: application/json
```
```json
{
  "venue_id": "8c2f1b40-7a3e-4f21-9c77-5b1f2a9e6d10",
  "start_at": "2026-10-17T15:00:00-06:00",
  "end_at": "2026-10-18T02:00:00-06:00",
  "guests": 120,
  "slot_kind": "FULL_DAY",
  "extras": ["asador", "sonido"],
  "quote_total_cents": 1166250
}
```
```json
{
  "id": "b71c9a4e-1f0d-4a6e-9d2b-3f5a7c8e9b01",
  "folio": "HD-7K3M21",
  "venue_id": "8c2f1b40-7a3e-4f21-9c77-5b1f2a9e6d10",
  "status": "ACTIVE",
  "start_at": "2026-10-17T21:00:00+00:00",
  "end_at": "2026-10-18T08:00:00+00:00",
  "venue_timezone": "America/Merida",
  "guests": 120,
  "slot_kind": "FULL_DAY",
  "total_cents": 1166250,
  "deposit_cents": 349875,
  "currency": "MXN",
  "expires_at": "2026-10-01T18:15:00+00:00",
  "seconds_remaining": 900,
  "can_extend": true,
  "quote_snapshot": { "engine_version": "1.0.0", "totals": { "total_cents": 1166250 } },
  "venue_name": "Palapa Xcambó",
  "quote": { "lines": [{ "concept": "Renta del espacio", "amount_cents": 840000 }], "warnings": [] },
  "next_step": "payment",
  "idempotent_replay": false
}
```

### Conflicto de disponibilidad (edge case A1)

```json
{
  "error": {
    "code": "VENUE_NOT_AVAILABLE",
    "message": "Alguien más apartó ese horario hace un segundo. Elige otra fecha u otro espacio.",
    "details": {
      "conflicts": [{ "source": "hold", "start_at": "2026-10-17T21:00:00+00:00", "end_at": "2026-10-18T08:00:00+00:00" }],
      "suggested": [
        { "start_at": "2026-10-18T21:00:00+00:00", "end_at": "2026-10-19T08:00:00+00:00", "label": "+1 día(s)" },
        { "start_at": "2026-10-16T21:00:00+00:00", "end_at": "2026-10-17T08:00:00+00:00", "label": "-1 día(s)" }
      ]
    },
    "request_id": "req_9f2c41ab7d5e3c10"
  }
}
```

### Apartado vencido al pagar (edge case A2)

```json
{
  "error": {
    "code": "HOLD_EXPIRED",
    "message": "Tu apartado venció y el horario se liberó. Vuelve a apartarlo para pagar.",
    "details": { "hold_id": "b71c9a4e-...", "folio": "HD-7K3M21" },
    "request_id": "req_1a2b3c4d5e6f7a8b"
  }
}
```

### Webhook de pago duplicado (edge case A8) — responde 200 a propósito

```json
{ "received": true, "event_id": null, "duplicated": true, "action": "ignored" }
```

### Validación de cupón (no rompe el flujo)

```json
{ "valid": false, "code": "FIESTA10", "discount_cents": 0, "message": "Este cupón alcanzó su límite de usos.", "reason": "COUPON_EXHAUSTED" }
```

## 3.4 Códigos de error (contrato público)

| Grupo | Códigos |
|---|---|
| Genéricos | `VALIDATION_ERROR` · `UNAUTHORIZED` · `FORBIDDEN` · `NOT_FOUND` · `CONFLICT` · `RATE_LIMITED` · `INTERNAL_ERROR` · `SERVICE_DEGRADED` |
| Identidad | `INVALID_CREDENTIALS` · `EMAIL_ALREADY_REGISTERED` · `PHONE_ALREADY_REGISTERED` · `EMAIL_NOT_VERIFIED` · `PHONE_NOT_VERIFIED` · `OTP_INVALID` · `OTP_EXPIRED` · `OTP_TOO_MANY_ATTEMPTS` · `REFRESH_TOKEN_INVALID` · `REFRESH_TOKEN_REUSED` · `PASSWORD_RESET_TOKEN_INVALID` · `TOS_NOT_ACCEPTED` · `UNDERAGE_NOT_ALLOWED` · `ACCOUNT_SUSPENDED` |
| Proveedor | `PROVIDER_NOT_APPROVED` · `PROVIDER_PAYOUTS_NOT_ENABLED` · `VERIFICATION_DOCUMENT_INVALID` · `VERIFICATION_ALREADY_PENDING` |
| Catálogo | `VENUE_NOT_FOUND` · `VENUE_SUSPENDED` · `VENUE_NOT_AVAILABLE` · `VENUE_DUPLICATE` · `CAPACITY_EXCEEDED` · `ADDRESS_NOT_GEOCODABLE` · `MEDIA_LIMIT_REACHED` · `MEDIA_TYPE_NOT_ALLOWED` |
| Tiempo y precio | `AVAILABILITY_RULE_CONFLICT` · `BLACKOUT_OVERLAPS_CONFIRMED_BOOKING` · `DATE_IN_PAST` · `INVALID_DURATION` · `MIN_HOURS_NOT_MET` · `GUESTS_OUT_OF_RANGE` · `NO_PRICING_RULE` · `TIMEZONE_INVALID` · `QUOTE_EXPIRED` |
| Reserva | `HOLD_EXPIRED` · `HOLD_NOT_FOUND` · `HOLD_LIMIT_REACHED` · `HOLD_ALREADY_CONVERTED` · `BOOKING_NOT_FOUND` · `INVALID_STATE_TRANSITION` · `BOOKING_ALREADY_STARTED` · `RESCHEDULE_TARGET_UNAVAILABLE` · `CHECK_IN_NOT_ALLOWED_YET` · `QR_TOKEN_INVALID` · `CONTRACT_NOT_READY` |
| Pagos | `PAYMENT_DUPLICATED` · `PAYMENT_NOT_FOUND` · `PAYMENT_FAILED` · `PAYMENT_REQUIRES_ACTION` · `CARD_DECLINED` · `CURRENCY_MISMATCH` · `AMOUNT_MISMATCH` · `WEBHOOK_SIGNATURE_INVALID` · `WEBHOOK_EVENT_DUPLICATED` · `REFUND_NOT_ALLOWED` · `REFUND_EXCEEDS_PAYMENT` · `REFUND_ALREADY_EXISTS` · `PAYOUT_FROZEN` |
| Cupones | `COUPON_NOT_FOUND` · `COUPON_EXPIRED` · `COUPON_EXHAUSTED` · `COUPON_NOT_APPLICABLE` · `COUPON_ALREADY_REDEEMED` |
| Contenido | `REVIEW_NOT_ALLOWED` · `REVIEW_ALREADY_EXISTS` · `REVIEW_WINDOW_CLOSED` · `REVIEW_UNDER_MODERATION` · `THREAD_NOT_FOUND` · `MESSAGE_BLOCKED_BY_MODERATION` · `CONTACT_SHARING_FLAGGED` |
| Disputas/admin | `DISPUTE_NOT_FOUND` · `DISPUTE_ALREADY_OPEN` · `DISPUTE_DEADLINE_PASSED` · `EVIDENCE_LIMIT_REACHED` · `IDEMPOTENCY_KEY_REQUIRED` · `IDEMPOTENCY_KEY_REUSED` · `IDEMPOTENCY_IN_PROGRESS` |

> Los códigos y sus mensajes por defecto están en `app/core/errors.py`. La UI puede reemplazar el
> texto, pero **decide por código**: renombrar uno es un breaking change.
