# 01 · Resumen ejecutivo, supuestos y ADRs

## 1.1 Qué es Quinta

Marketplace de **dos lados** para rentar espacios de evento en México (y LatAm hispanohablante):
palapas de playa, quintas, salones de fiestas, jardines, terrazas y rooftops. El cliente busca por
**fecha y tipo de celebración**, no por nombre de negocio; el proveedor publica su calendario, cobra
anticipo y gestiona la operación del día.

Lo que lo separa de un clon de reservas genérico:

1. **La fecha es la unidad de búsqueda.** Todo listado responde "¿está libre mi sábado?" antes que el
   precio. La disponibilidad se calcula con reglas por venue, blackouts, temporadas y buffers.
2. **Apartado con precio congelado.** El `BookingHold` es un recibo digital con folio, TTL y
   snapshot de cotización (edge case A5). Es el objeto central de la UI.
3. **Renta por bloques, no por hora suelta.** Día completo, medio día, bloque nocturno, multi-día
   (bodas de fin de semana) y cruce de medianoche con buffer de limpieza (A4).
4. **Split de pagos real.** Stripe Connect: el proveedor cobra, la plataforma retiene comisión, el
   depósito de garantía queda en retención y se libera por job (B14).
5. **Confianza operada.** Verificación de documentos del proveedor, reseñas sólo post-evento,
   detección de bypass de plataforma en mensajes con revisión humana antes de sancionar (C19).

## 1.2 Alcance del MVP (Fase 1) y lo que se difiere

| Incluido en MVP | Diferido |
|---|---|
| Búsqueda geoespacial + disponibilidad por fecha | Mensajería avanzada (adjuntos, plantillas, traducción) — F3 |
| Perfil de proveedor + verificación documental | Payouts automáticos semanales (MVP: release manual por admin) |
| Hold + reserva + contrato PDF + reseñas | Multi-moneda / multi-idioma — F3 |
| Pagos con tarjeta (Stripe) + anticipo + reembolsos escalonados | CFDI 4.0 automático (MVP: factura manual asistida) — F2 |
| Notificaciones push/email + recordatorios | API pública + webhooks para terceros — F3 |
| Disputas con evidencia y congelación de payout | Subasta de fechas, precios dinámicos — fuera de alcance |
| Panel de proveedor (ocupación, agenda, ingresos) | App de staff/portero (check-in en puerta) — F2 |

## 1.3 Supuestos (si alguno cambia, cambia el diseño)

| # | Supuesto | Consecuencia si es falso |
|---|---|---|
| S1 | El cobro principal es **en línea**; el efectivo/SPEI offline es excepción | Se necesita conciliación manual y estados `PENDING_OFFLINE` |
| S2 | Cada venue tiene **una** zona horaria IANA fija | Multi-tz por venue requeriría reglas por recurso |
| S3 | La unidad mínima de renta es **bloque** (medio día/día), no hora | Cambia el modelo de `PricingRule` y la grilla de UI |
| S4 | Los proveedores son personas físicas o morales **mexicanas** en MVP | Fiscalidad y Connect cambian por país |
| S5 | El pago se hace contra un `PaymentIntent` **uno a uno** con el booking | Marketplace multi-item (varios venues) requeriría carrito |
| S6 | Las imágenes se sirven desde R2/S3 con URL pre-firmada; CDN delante | Sin CDN, `VenueMedia` debe cachear variantes |
| S7 | Un proveedor puede tener N venues, pero **un** `ProviderProfile` | Multi-marca requeriría `ProviderProfile` ↔ `Venue` many-to-many |
| S8 | La disponibilidad se publica con reglas; no hay integración con PMS externos | Integración requeriría webhooks de sync bidireccional |
| S9 | Aceptamos el costo de una constraint de exclusión en Postgres como fuente de verdad | Sin Postgres+PostGIS, la garantía anti-doble-reserva se degrada a lock en app |
| S10 | Los eventos son **sin menores de edad como titulares**; el titular debe ser mayor | Se necesita flujo de tutor legal |

## 1.4 Decisiones de arquitectura (ADRs breves)

### ADR-001 · Monolito modular con FastAPI, no microservicios
**Contexto:** equipo pequeño, dominio con transacciones fuertes (hold → pago → reserva) y una sola
base de datos relacional. **Decisión:** monolito modular (routers → services → repositorios
SQLAlchemy) desplegable en N réplicas sin estado, con Celery aparte para trabajo asíncrono.
**Consecuencias:** una transacción ACID cubre hold+reserva; el escalado horizontal se hace por
réplicas de API y por workers; el acoplamiento se controla con módulos `services/*` que no importan
routers.

### ADR-002 · Constraint de exclusión en PostgreSQL como única autoridad anti-solapamiento
**Contexto:** dos clientes intentan el mismo sábado; los locks en aplicación se filtran con
reintentos y latencia. **Decisión:** `EXCLUDE USING gist (venue_id WITH =, tstzrange(start_at,end_at)
WITH &&)` sobre `bookings` y `booking_holds`, con `btree_gist` y `tstzrange(...,'[)')`. La aplicación
traduce `23P01` (exclusion_violation) a `VENUE_NOT_AVAILABLE` (HTTP 409). Se añade
`pg_advisory_xact_lock(hash(venue_id))` sólo para reducir el ruido de conflictos, nunca como garantía.
**Consecuencias:** imposible doble reserva incluso con bug de aplicación; los holds expirados deben
limpiarse por job (si no, bloquean slots) → job `expire_holds` cada minuto.

### ADR-003 · Holds y bookings en tablas separadas, conversión transaccional
**Contexto:** el hold tiene TTL, folio y snapshot; el booking es el contrato. **Decisión:**
`booking_holds` guarda el apartado (con su propia constraint de exclusión); al confirmar, en **una
transacción** se hace `DELETE hold` + `INSERT booking`, con `SELECT ... FOR UPDATE` sobre el venue.
**Consecuencias:** dos tablas con la misma constraint (el solape entre ambas también es imposible);
el histórico de holds se conserva para auditoría del embudo (`BookingHold.status`).

### ADR-004 · Snapshot de cotización en el booking/hold
**Contexto:** los precios y reglas cambian (E27, A5). **Decisión:** `quote_snapshot` JSONB con
desglose congelado, reglas aplicadas, versión del motor de precios y `currency`. La factura y el
contrato se generan desde el snapshot, no desde las reglas vivas.
**Consecuencias:** grandfathering automático; hay que versionar el motor (`PRICING_ENGINE_VERSION`) y
migrar snapshots sólo si cambia el esquema del JSON.

### ADR-005 · Idempotencia en base de datos, no sólo en Redis
**Contexto:** reintentos del cliente móvil, webhooks duplicados y red inestable en la playa.
**Decisión:** tabla `idempotency_keys` con `UNIQUE(user_id, endpoint, key)`, `request_hash`,
`response_body`, `status`; Redis sólo da el lock corto. Misma `Idempotency-Key` con body distinto →
`409 IDEMPOTENCY_KEY_REUSED`.
**Consecuencias:** durabilidad del resultado (el reintento devuelve la misma respuesta aunque el
worker haya caído); requiere TTL de limpieza (30 días) y no sirve para endpoints no deterministas
(subida de archivos usa `multipart` + hash de contenido).

### ADR-006 · Pagos con Stripe Connect (destination charges) y MercadoPago como adaptador
**Contexto:** split a proveedores y disponibilidad de Stripe en MX. **Decisión:** interfaz
`PaymentGateway` con dos adaptadores (`stripe`, `mercadopago`); `destination_charges` con
`application_fee_amount` y `transfer_data.destination`; el depósito de garantía se cobra como
`PaymentIntent` separado marcado `is_deposit`.
**Consecuencias:** el dominio no conoce Stripe; los webhooks se normalizan a eventos internos
(`payment.succeeded`, `payment.failed`, `charge.disputed`); el `Payout` se materializa por job.

### ADR-007 · Colas Celery separadas por criticidad
**Contexto:** un pico de notificaciones no debe retrasar la expiración de holds. **Decisión:** colas
`payments`, `notifications`, `default`, con `priority` y workers dedicados; `beat` para cron.
**Consecuencias:** más contenedores; los jobs son idempotentes y toman locks por booking
(`SELECT ... FOR UPDATE SKIP LOCKED`) para tolerar ejecuciones dobles.

### ADR-008 · Búsqueda: PostGIS + trigram, sin motor externo
**Contexto:** catálogo de decenas de miles de venues; el MVP no justifica Elastic. **Decisión:**
`geography(Point,4326)` + índice GiST para radio, `pg_trgm` para texto, filtros por `EXISTS` sobre
amenidades y `NOT EXISTS` sobre blackouts/holds; caché Redis de 60 s para `GET /venues` y
`/search/suggestions`.
**Consecuencias:** radio grande (>80 km) se limita y se ordena por distancia con `ST_DWithin`;
si el catálogo crece a millones, se migra a OpenSearch (F3).

### ADR-009 · Errores con códigos estables y `request_id`
**Contexto:** la app móvil decide UX por código, no por texto. **Decisión:** jerarquía `AppError`
(código, HTTP status, mensaje es-MX, `details`) y handler global que añade `request_id` y lo
correlaciona con logs y `AuditLog`.
**Consecuencias:** los códigos son contrato público (documentados en `docs/03`); renombrar uno es
breaking change.

### ADR-010 · Anonimización vs. retención fiscal
**Contexto:** LFPDPPP (ARCO) y obligación contable (5 años). **Decisión:** `DELETE /me` hace
soft-delete + anonimización de PII (`users.pii_erased_at`) y **conserva** `Payment`, `Invoice`,
`Payout`, `Refund` con seudónimo fiscal; los documentos de verificación se borran del bucket.
**Consecuencias:** dos relojes de retención; los reportes financieros no pueden depender de PII.

### ADR-011 · React Native con Expo + expo-router, no CLI puro
**Contexto:** un equipo, iOS+Android, necesidad de OTA para corregir copy de precios. **Decisión:**
Expo (managed con prebuild para módulos nativos de mapas) + `expo-router` (file-based) +
TanStack Query + Zustand para sesión; Reanimated para motion con `prefers-reduced-motion` equivalente
(`AccessibilityInfo.isReduceMotionEnabled`).
**Consecuencias:** los módulos nativos exigen `expo prebuild`; Maestro cubre E2E sin Detox.

### ADR-012 · Webhooks salientes con HMAC y reintentos con backoff
**Contexto:** proveedores grandes quieren integrar su CRM. **Decisión:** firma
`X-Quinta-Signature: t=<unix>,v1=<hmac_sha256(t + "." + body)>`, ventana de 5 min, reintentos
1m/5m/30m/2h/12h (5 intentos), `WebhookDelivery` con respuesta y estado, endpoint de replay admin.
**Consecuencias:** el consumidor debe ser idempotente por `event.id`; guardamos el payload firmado.

## 1.5 Métricas de éxito (norte del producto)

| Métrica | Definición | Meta MVP |
|---|---|---|
| Time-to-book | De abrir la app a `CONFIRMED` | < 6 min p50 |
| Conversión hold → pagado | `BookingHold` convertidos | > 55% |
| Doble reserva | Solapes confirmados | 0 (invariante de DB) |
| Ocupación de fin de semana | Slots sáb/dom vendidos | > 45% en venues activos |
| Tasa de disputa | Disputas / reservas pagadas | < 1.5% |
| Latencia búsqueda p95 | `GET /venues` con geo | < 400 ms |
| Reseñas por evento | Reseñas / bookings `COMPLETED` | > 30% |
