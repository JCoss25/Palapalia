# 13 · Checklist de aceptación por endpoint

Se marca cuando: **implementado**, **documentado en OpenAPI**, **con RBAC probado**,
**con test automatizado** y **con copy de error en es-MX**.

## Auth e identidad

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST /auth/register` | ✅ | ✅ | público | ✅ `test_auth_rbac.py` | ✅ `UNDERAGE_NOT_ALLOWED`, `EMAIL_ALREADY_REGISTERED` |
| `POST /auth/login` | ✅ | ✅ | público | ✅ | ✅ `INVALID_CREDENTIALS`, `ACCOUNT_SUSPENDED` |
| `POST /auth/refresh` | ✅ | ✅ | público | ✅ reuso de familia | ✅ `REFRESH_TOKEN_REUSED` |
| `POST /auth/logout` | ✅ | ✅ | cliente | ⬜ | — |
| `POST /auth/forgot-password` | ✅ | ✅ | público | ⬜ | ✅ respuesta uniforme |
| `POST /auth/reset-password` | ✅ | ✅ | público | ⬜ | ✅ `PASSWORD_RESET_TOKEN_INVALID` |
| `POST /auth/verify-email` | ✅ | ✅ | token | ⬜ | — |
| `POST /auth/verify-phone` | ✅ | ✅ | OTP | ✅ | ✅ `OTP_INVALID/EXPIRED/TOO_MANY_ATTEMPTS` |
| `POST /auth/otp/resend` | ✅ | ✅ | cliente | ⬜ | ✅ |
| `GET/PATCH/DELETE /me` | ✅ | ✅ | cliente | ✅ borrado | ✅ retención fiscal explicada |
| `GET /me/export` | ✅ | ✅ | cliente | ⬜ | ✅ |
| `GET /me/favorites` | ✅ | ✅ | cliente | ⬜ | — |
| `GET /me/bookings` | ✅ | ✅ | cliente/proveedor | ✅ | — |
| `GET /admin/users`, `PATCH /admin/users/{id}`, `POST /admin/users/{id}/ban` | ✅ | ✅ | ADMIN | ✅ ban | ✅ |
| `POST /admin/users/{id}/role` | ✅ | ✅ | ADMIN | ⬜ | — |

## Proveedores

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST /providers/apply` | ✅ | ✅ | cliente | ⬜ | ✅ `TOS_NOT_ACCEPTED` |
| `GET/PATCH /providers/me` | ✅ | ✅ | proveedor | ⬜ | ✅ |
| `GET /providers/me/verification-status` | ✅ | ✅ | proveedor | ⬜ | ✅ motivos de bloqueo |
| `POST /providers/me/verification-documents` | ✅ | ✅ | proveedor | ✅ hash duplicado | ✅ |
| `GET /providers/me/dashboard` | ✅ | ✅ | proveedor aprobado | ⬜ | — |
| `GET /providers/{id}` | ✅ | ✅ | público | ⬜ | ✅ |
| `GET /providers/{id}/rating-summary` | ✅ | ✅ | público | ⬜ | — |
| `POST /admin/providers/{id}/approve` | ✅ | ✅ | ADMIN | ⬜ | ✅ |

## Venues

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST /venues` | ✅ | ✅ | proveedor aprobado | ✅ geocode degradado | ✅ `ADDRESS_NOT_GEOCODABLE`, `VENUE_DUPLICATE` |
| `GET /venues` (21 filtros) | ✅ | ✅ | público | ✅ radio, disponibilidad | ✅ |
| `GET /venues/{id}` | ✅ | ✅ | público | ⬜ | ✅ |
| `PATCH /venues/{id}` | ✅ | ✅ | dueño | ⬜ | ✅ `PROVIDER_PAYOUTS_NOT_ENABLED` |
| `DELETE /venues/{id}` | ✅ | ✅ | dueño | ⬜ | ✅ `CONFLICT` con conteo |
| `POST /venues/{id}/media` | ✅ | ✅ | dueño | ⬜ | ✅ `MEDIA_TYPE_NOT_ALLOWED`, `MEDIA_LIMIT_REACHED` |
| `PATCH /venues/{id}/media/reorder`, `DELETE .../{mediaId}` | ✅ | ✅ | dueño | ⬜ | ✅ |
| `POST/DELETE /venues/{id}/favorite` | ✅ | ✅ | cliente | ⬜ | — |
| `POST /admin/venues/{id}/suspend` | ✅ | ✅ | ADMIN | ⬜ | ✅ |
| `POST /admin/venues/merge` | ✅ | ✅ | ADMIN | ⬜ | ✅ |

## Disponibilidad y precios

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `GET/PUT /venues/{id}/availability-rules` | ✅ | ✅ | dueño | ✅ solapes | ✅ `AVAILABILITY_RULE_CONFLICT` |
| `GET /venues/{id}/availability` | ✅ | ✅ | público | ⬜ | ✅ motivos (`booked`, `blackout`, `past`) |
| `POST/DELETE /venues/{id}/blackout-dates` | ✅ | ✅ | dueño | ⬜ | ✅ `BLACKOUT_OVERLAPS_CONFIRMED_BOOKING` |
| `GET/PUT /venues/{id}/pricing-rules` | ✅ | ✅ | dueño | ⬜ | ✅ `NO_PRICING_RULE` |
| `GET /venues/{id}/quote` | ✅ | ✅ | opcional | ✅ desglose, DST, cupones | ✅ `GUESTS_OUT_OF_RANGE`, `MIN_HOURS_NOT_MET` |

## Reservas

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST /bookings/hold` | ✅ | ✅ | cliente | ✅ carrera, idempotencia, buffer, límite | ✅ `VENUE_NOT_AVAILABLE` + alternativas, `HOLD_LIMIT_REACHED` |
| `GET /bookings/holds/{id}`, `POST .../extend`, `POST .../release` | ✅ | ✅ | cliente | ✅ extensión única | ✅ `HOLD_EXPIRED` |
| `POST /bookings` | ✅ | ✅ | cliente | ✅ confirmación, hold vencido | ✅ `PAYMENT_DUPLICATED` |
| `GET /bookings/{id}` | ✅ | ✅ | partes | ✅ intruso recibe 403 | ✅ |
| `GET /bookings/{id}/contract` | ✅ | ✅ | partes | ⬜ | ✅ `CONTRACT_NOT_READY` |
| `GET /bookings/{id}/refund-preview` | ✅ | ✅ | partes | ✅ | ✅ explicación de política |
| `POST /bookings/{id}/cancel` | ✅ | ✅ | partes | ✅ 100% / 0% | ✅ |
| `POST /bookings/{id}/reschedule` | ✅ | ✅ | partes | ✅ destino ocupado | ✅ `RESCHEDULE_TARGET_UNAVAILABLE` |
| `POST /bookings/{id}/check-in` | ✅ | ✅ | partes | ⬜ | ✅ `CHECK_IN_NOT_ALLOWED_YET` |
| `POST /bookings/{id}/check-out` | ✅ | ✅ | proveedor | ⬜ | ✅ evidencia para daños |
| `POST /bookings/{id}/complete` | ✅ | ✅ | proveedor | ⬜ | ✅ |
| `POST /bookings/{id}/no-show` | ✅ | ✅ | proveedor | ✅ guarda de evidencia | ✅ |
| `POST /bookings/{id}/dispute` | ✅ | ✅ | partes | ✅ | ✅ `DISPUTE_ALREADY_OPEN` |
| `PATCH /admin/bookings/{id}/resolve` | ✅ | ✅ | ADMIN | ⬜ | ✅ `DISPUTE_DEADLINE_PASSED` |

## Pagos

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST /payments/intent` | ✅ | ✅ | cliente | ✅ duplicado | ✅ `CARD_DECLINED`, `PAYMENT_REQUIRES_ACTION` |
| `POST /payments/webhook` | ✅ | ✅ | firma | ✅ firma, duplicado, pago tardío | ✅ `WEBHOOK_SIGNATURE_INVALID` |
| `GET /payments/{id}`, `GET /me/payments` | ✅ | ✅ | partes | ⬜ | ✅ |
| `POST /refunds`, `GET /refunds/{id}` | ✅ | ✅ | cliente/soporte | ✅ tope | ✅ `REFUND_EXCEEDS_PAYMENT` |
| `GET /providers/me/payouts` | ✅ | ✅ | proveedor | ⬜ | ✅ |
| `GET /admin/payouts`, `POST /admin/payouts/{id}/release` | ✅ | ✅ | ADMIN | ⬜ | ✅ `PAYOUT_FROZEN` |
| `POST /invoices`, `GET /invoices/{id}` | ✅ | ✅ | cliente | ⬜ | ✅ |

## Reseñas, mensajería, notificaciones, catálogo, cupones, admin y webhooks

| Endpoint | Impl | OpenAPI | RBAC | Test | Copy |
|---|---|---|---|---|---|
| `POST/PATCH/DELETE /reviews` | ✅ | ✅ | autor | ✅ riesgo de reseña | ✅ `REVIEW_NOT_ALLOWED`, `REVIEW_WINDOW_CLOSED` |
| `POST /reviews/{id}/reply`, `POST /reviews/{id}/report` | ✅ | ✅ | proveedor/cliente | ⬜ | ✅ `MESSAGE_BLOCKED_BY_MODERATION` |
| `GET /venues/{id}/reviews`, `/rating-summary` | ✅ | ✅ | público | ⬜ | — |
| `PATCH /admin/reviews/{id}` | ✅ | ✅ | staff | ⬜ | ✅ |
| `POST/GET /threads`, `GET/POST /threads/{id}/messages`, `PATCH /messages/{id}/read` | ✅ | ✅ | partes | ✅ anti-bypass | ✅ `CONTACT_SHARING_FLAGGED` |
| `WS /ws/threads/{id}` | ✅ | ✅ (fuera de OpenAPI) | partes | ⬜ | cierre 1008 |
| `GET /notifications`, `PATCH /{id}/read`, `PATCH /read-all` | ✅ | ✅ | cliente | ⬜ | — |
| `GET/PUT /me/notification-preferences`, `POST/DELETE /me/push-devices` | ✅ | ✅ | cliente | ⬜ | — |
| `GET /categories`, `/amenities`, `/event-types` | ✅ | ✅ | público | ⬜ | — |
| `GET /search/suggestions`, `GET /home/featured` | ✅ | ✅ | público | ⬜ | ✅ etiqueta Patrocinado |
| `POST /coupons/validate` | ✅ | ✅ | cliente | ✅ | ✅ `COUPON_EXHAUSTED`, `COUPON_NOT_APPLICABLE` |
| `GET/POST/PATCH/DELETE /admin/coupons` | ✅ | ✅ | ADMIN | ⬜ | ✅ |
| `POST/DELETE /admin/categories`, `/amenities`, `/event-types` | ✅ | ✅ | ADMIN | ⬜ | ✅ soft-delete con migración |
| `GET /admin/metrics`, `/audit-logs`, `/audit-logs/verify`, `/disputes`, `/venue-reports` | ✅ | ✅ | ADMIN/SUPPORT | ⬜ | — |
| `POST /admin/impersonate` | ✅ | ✅ | ADMIN | ⬜ | ✅ advertencia de auditoría |
| `GET /admin/webhooks/events`, `POST /{id}/replay`, `GET /admin/webhooks/deliveries` | ✅ | ✅ | ADMIN | ⬜ | — |
| `POST/GET/DELETE /webhooks/subscriptions`, `GET /webhooks/deliveries` | ✅ | ✅ | proveedor | ⬜ | ✅ límite de 5 |

## Transversales

| Requisito | Estado | Evidencia |
|---|---|---|
| Errores con códigos estables | ✅ | `core/errors.py`, `docs/03` §3.4 |
| `Idempotency-Key` en POST críticos | ✅ | `services/idempotency.py` + pruebas |
| Rate limiting con cabeceras | ✅ | `core/ratelimit.py` |
| Paginación por cursor | ✅ | `core/pagination.py` |
| Auditoría encadenada | ✅ | `services/audit.py` + `GET /admin/audit-logs/verify` |
| Soft-delete en todo lo borrable | ✅ | `SoftDeleteMixin` |
| RBAC probado | ⚠️ parcial | Faltan pruebas de `check-in`, `payouts`, `webhooks` |
| OpenAPI 3.1 sin cambios inesperados | ⬜ | Añadir job `openapi` al CI |
| Cobertura ≥ 80% en `services/*` | ⚠️ parcial | Faltan pruebas de `notifications` y `webhooks_out` |
| Contrato de aceptación de la app (Maestro) | ✅ | `e2e/maestro/booking_flow.yaml` |

## Pendientes explícitos antes de producción

1. `openapi.json` versionado y comparado en CI.
2. Pruebas de integración para `check-in`/`check-out`, payouts y suscripciones de webhooks.
3. `seed.py` con catálogo inicial (categorías, amenidades, tipos de evento) idempotente.
4. Sustituir las fotos de referencia del board por media real de los venues.
5. Confirmar con el contador el modelo de facturación de la intermediación (comisión vs. renta).
6. Habilitar Stripe Connect en modo live y validar un pago real con reembolso.
