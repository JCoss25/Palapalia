# 11 · Seguridad, privacidad y cumplimiento (México)

## 11.1 OWASP API Security Top 10 (2023) aplicado

| Riesgo | Cómo se mitiga en Quinta | Dónde |
|---|---|---|
| **API1 · Broken Object Level Authorization** | Cada endpoint carga el recurso y compara con el usuario del JWT (`_get_booking_for_actor`, `_load_venue_for_owner`, `_thread_for_participant`). El ID de la ruta nunca autoriza. | `app/api/v1/*.py`, `app/api/deps.py` |
| **API2 · Broken Authentication** | bcrypt coste 12, bloqueo tras 8 intentos (15 min), refresh con rotación por familia y detección de reuso (revoca toda la familia), OTP hasheado con `salt` e intentos limitados | `core/security.py`, `api/v1/auth.py` |
| **API3 · Broken Object Property Level Authorization** | Pydantic v2 con `extra="forbid"`; los esquemas de salida no exponen campos internos (`ProviderPublicOut` sin RFC ni estado de pagos, `BookingDetailOut.qr_token` sólo al titular) | `app/schemas/*` |
| **API4 · Unrestricted Resource Consumption** | Rate limit por cubeta, `limit ≤ 50`, `statement_timeout` de 8 s, radio de búsqueda ≤ 120 km, `hold_max_active_per_user`, paginación por cursor | `core/ratelimit.py`, `core/pagination.py` |
| **API5 · Broken Function Level Authorization** | RBAC explícito por endpoint con `require_roles(...)`; `AdminUser`/`StaffUser`/`PublishedProvider` como dependencias; rutas admin en routers separados | `api/deps.py`, `api/v1/router.py` |
| **API6 · Unrestricted Access to Sensitive Business Flows** | Holds con TTL y límite por usuario; reservas inmediatas sólo con cuenta de pagos verificada; verificación documental antes de publicar | `services/bookings.py`, `providers.py` |
| **API7 · Server Side Request Forgery** | Webhooks salientes sólo a `https://` (validado en el esquema); sin URLs proporcionadas por el usuario en llamadas del servidor; la geocodificación va a endpoints fijos | `schemas/content.py`, `services/maps.py` |
| **API8 · Security Misconfiguration** | `SECRET_KEY` ≥ 32 caracteres obligatorio; CORS con lista explícita; HSTS sólo en prod; contenedor con usuario no root; `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` | `core/config.py`, `main.py`, `Dockerfile` |
| **API9 · Improper Inventory Management** | Una sola versión (`/api/v1`); `/docs` y `/redoc` deshabilitables por entorno; `openapi.json` versionado en CI; `/metrics` fuera del esquema | `main.py` |
| **API10 · Unsafe Consumption of Third-Party APIs** | Circuit breaker en mapas; firma obligatoria de webhooks entrantes; `Idempotency-Key` hacia el PSP; timeouts explícitos en toda llamada externa | `services/maps.py`, `services/payments.py` |

## 11.2 Controles adicionales

| Control | Implementación |
|---|---|
| Contraseñas | bcrypt (12 rondas), mínimo 8, lista negra de las más usadas |
| Tokens | HS256, `aud`/`iss` verificados, access 15 min, refresh 30 días rotado |
| Impersonación | TTL ≤ 120 min, motivo obligatorio, `ImpersonationSession` + `AuditLog` con `impersonated_by_id` |
| Auditoría | Append-only con hash encadenado (`prev_hash`/`entry_hash`) y verificación en `GET /admin/audit-logs/verify` |
| PII en logs | `mask_pii()` en login/OTP; `scrub()` en auditoría (nunca se guardan teléfonos, correos, RFC ni secretos) |
| Secretos | Sólo por variables de entorno; `.env` en `.gitignore`; el `secret` de webhooks se muestra una sola vez |
| Archivos | URL pre-firmada con `content-length-range`, validación de MIME y tamaño, EXIF limpiado, verificación de `sha256` |
| Documentos de verificación | Bucket privado; descarga siempre por URL firmada; se borran del bucket al anonimizar la cuenta |
| SQL | SQLAlchemy con parámetros enlazados; sin concatenación de SQL en el dominio |
| Dependencias | `pip-audit`/`npm audit` en CI; versiones fijadas |

## 11.3 Privacidad y cumplimiento (LFPDPPP)

### Datos que se tratan

| Categoría | Datos | Base legal | Retención |
|---|---|---|---|
| Identificación | Nombre, correo, teléfono, RFC (opcional) | Consentimiento + ejecución del contrato | Mientras la cuenta exista |
| Verificación de proveedor | INE/acta/CSF/comprobante de domicilio | Consentimiento expreso | Hasta 90 días después de terminar la relación |
| Financieros | Pagos, reembolsos, payouts, facturas, ledger | Obligación contractual y fiscal | **5 años** (CFF) |
| Operativos | Reservas, mensajes, reseñas, IP, user-agent | Interés legítimo + contrato | 24 meses (mensajes), indefinido (reseñas publicadas anonimizadas) |
| Auditoría | Acciones sensibles | Interés legítimo (seguridad) | 730 días |

### Derechos ARCO

| Derecho | Implementación |
|---|---|
| Acceso | `GET /me` + `GET /me/export` (JSON completo por URL firmada) |
| Rectificación | `PATCH /me`, `PATCH /providers/me` |
| Cancelación | `DELETE /me`: soft-delete + anonimización (`pii_erased_at`), borrado real de documentos, bloqueo si hay reservas futuras |
| Oposición | Preferencias de notificación por evento × canal; `marketing_opt_in` separado del T&C |
| Portabilidad | El mismo `GET /me/export` en JSON |
| Revocación del consentimiento | Se registra `tos_version` y fecha; al revocar marketing se detiene el envío |

### Aviso de privacidad y T&C versionados

`tos_version` se guarda en `users.tos_version` y en `contracts.tos_version`. Cambiar de versión exige
nuevo consentimiento en el siguiente inicio de sesión (flujo pendiente en F2).

### Qué **no** se anonimiza y por qué

`invoices`, `payments`, `payouts`, `ledger_entries` conservan el vínculo contable, pero el usuario
queda como seudónimo (`deleted+{uuid}@quinta.invalid`, sin nombre ni teléfono) y con
`retention_until`. Esto evita romper la contabilidad (edge case F32) y cumple con la obligación
fiscal mexicana.

## 11.4 CFDI 4.0 (facturación)

| Aspecto | MVP | Fase 2 |
|---|---|---|
| Solicitud | `POST /invoices` con RFC, régimen, uso y CP | Igual, con validación contra el padrón del SAT |
| Timbrado | Asistido: se registra la solicitud, soporte timbra con el PAC | Automático vía PAC (Facturama/SW Sapien) con webhook de resultado |
| Datos en la factura | `subtotal`, `tax`, `total` desde el snapshot del booking | Igual |
| Almacenamiento | XML + PDF en R2 con URL firmada, `retention_until` de 5 años | Igual |
| Cancelación | Manual con motivo SAT (01–04) | Automática al reembolsar con `uuid_fiscal` registrado |

Nota fiscal: la comisión de servicio se factura a nombre de Quinta; la renta del espacio la factura
el anfitrión (modelo de intermediación). Esto debe confirmarse con el contador antes de producción.

## 11.5 Seguridad de la app móvil

| Control | Implementación |
|---|---|
| Almacenamiento de tokens | `expo-secure-store` (Keychain/Keystore); el access vive en memoria |
| Transporte | HTTPS obligatorio; certificate pinning en F2 |
| Datos sensibles en pantalla | El QR de check-in es un token de alcance limitado (`scope: checkin`), con expiración de 36 h |
| Deep links | Esquema `quinta://`; las rutas sensibles verifican sesión antes de mostrar datos |
| Capturas | Se permiten (no hay datos de tarjeta en la app; el cobro ocurre en Stripe) |
| Logs de la app | Nunca se loguean tokens ni PII (`mask_pii` en el cliente donde aplica) |

## 11.6 Plan de respuesta

| Incidente | Detección | Respuesta |
|---|---|---|
| Fuga de credenciales | `LOGIN_FAILED` anómalos, alerta de rate limit | Bloqueo de cuenta, revocación de familias de refresh, aviso al usuario |
| Doble cobro | Métrica `payment_duplicated_total` > 0 | Reembolso automático (`refund_duplicate_payment`) + post-mortem |
| Falsificación documental | `risk_flags.duplicate_document` | Suspensión inmediata del venue + revisión humana en < 24 h |
| Chargeback masivo | `dispute_rate` sobre umbral | Congelamiento de payouts del proveedor + revisión de patrones |
| Caída del PSP | `/health` `payments: degraded` | Banner en la app, cambio a SPEI/pago directo, cola de reintentos |
