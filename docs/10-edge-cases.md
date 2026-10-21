# 10 · Casos límite → endpoint, validación, test y mensaje UX

Cada fila cierra el ciclo: **dónde se detecta**, **cómo se prueba** y **qué lee la persona**.
Los mensajes de UX son los textos por defecto de `app/core/errors.py` (la UI puede reescribirlos,
pero siempre decide por `code`).

## A. Concurrencia y tiempo

| # | Caso | Endpoint / dónde | Validación (diseño) | Test | Mensaje UX |
|---|---|---|---|---|---|
| A1 | Dos clientes al mismo slot | `POST /bookings/hold` | `pg_advisory_xact_lock(venue)` + `EXCLUDE USING gist`; `23P01` → 409 | `test_dos_clientes_compiten_por_el_mismo_slot`, `test_holds_solapados_son_imposibles_a_nivel_db`, k6 `hold_race.js` | «Alguien más apartó ese horario hace un segundo. Elige otra fecha u otro espacio.» + 3 alternativas con fecha |
| A2 | Hold vence mientras se paga | `POST /payments/webhook` + job `reconcile_expired_hold_payment` | Si el hold no está `ACTIVE` ⇒ `REFUNDED` + reembolso automático; nunca se confirma | `test_pago_tardio_sobre_hold_expirado_se_reconcilia` | «Tu apartado venció antes de confirmarse, así que reembolsamos el cobro (…). Vuelve a apartar si sigues interesado.» |
| A3 | Zonas horarias y DST | `build_quote` / `validate_window` | UTC en BD + `timezone` IANA; duración = tiempo real (no horas de reloj) | `test_duracion_real_es_inmune_al_cambio_de_horario` | La UI muestra la hora del venue con su zona; si la duración real difiere, se indica |
| A4 | Cruce de medianoche y multi-día | `POST /bookings/hold` | `crosses_midnight` + buffer aplicado a `blocked_from/to`; multi-día requiere `allows_multi_day` | `test_reserva_cruza_medianoche_cuenta_como_un_dia`, `test_buffer_impide_eventos_pegados` | «Tu evento cruza la medianoche: cuenta como un solo día, sin cargos extra.» · «Se reservan 60 min de limpieza después de tu evento.» |
| A5 | Cambio de precio tras el hold | `POST /bookings/hold` con `quote_total_cents` | El hold congela `quote_snapshot`; el total del cliente se compara con el del servidor | `test_verificar_total_detecta_cambio_de_precio`, `test_cotizacion_es_determinista` | «El precio cambió desde que viste la cotización. Revisa el nuevo desglose.» |
| A6 | Reserva en el pasado / blackout posterior al hold | `validate_window`, `POST /venues/{id}/blackout-dates` | `DATE_IN_PAST`; blackout sobre reservas confirmadas exige `force=true` | `test_reserva_en_el_pasado`, `test_curfew_rechaza_salida_tardia` (y prueba de blackout en `test_venues_search.py`) | «Esa fecha ya pasó. Elige una fecha futura.» · «No puedes cerrar una fecha que ya tiene una reserva confirmada.» |
| A7 | Pago duplicado por reintento | `POST /payments/intent` | Búsqueda de pago vivo con mismo `kind`+`amount`; `UNIQUE(gateway, provider_intent_id)` | `test_pago_duplicado_en_la_api_responde_409` | «Este pago ya fue registrado; no se cobró dos veces.» |
| A8 | Webhook duplicado / fuera de orden | `POST /payments/webhook` | `applied_events` por `event_id`; estado final gana | `test_webhook_duplicado_no_cobra_dos_veces` | Sin mensaje al usuario (idempotente); queda en auditoría |

## B. Pagos

| # | Caso | Endpoint | Validación | Test | Mensaje UX |
|---|---|---|---|---|---|
| B9 | Fallo de pago, 3DS/SCA, tarjeta rechazada | `POST /payments/intent` | Se guarda `requires_action_reason` y `next_action_url`; `CARD_DECLINED` mapeado | `test_payments_webhook.py` (ramas `payment.failed`) + job `notify_payment_failure` | «El banco rechazó la tarjeta. Prueba con otra o usa transferencia SPEI.» · «Tu banco pide confirmar la compra…» |
| B10 | Reembolso parcial vs. total y política escalonada | `POST /refunds`, `GET /bookings/{id}/refund-preview` | `refund_tier(days)`: 100/50/0; tope `refunded_cents ≤ amount_cents` | `test_politica_escalonada`, `test_reembolso_no_puede_exceder_el_pago` | «Reembolso del 50%: cancelaste entre 7 y 30 días antes del evento.» |
| B11 | Disputa / chargeback | `POST /bookings/{id}/dispute`, `PATCH /admin/bookings/{id}/resolve` | Congela payouts; `deadline_at` de 7 días; evidencias obligatorias para daños | `test_state_machine.py` (disputa) + pruebas de admin | «Tu pago al anfitrión queda retenido mientras revisamos.» |
| B12 | Proveedor sin cuenta de pagos completa | `POST /payments/intent`, `PATCH /venues/{id}` | `payouts_enabled=false` ⇒ no cobra en línea; se permite publicar con `offline_booking_only` | `test_provider_payouts_not_enabled` (en `test_venues_search.py`/`test_auth_rbac.py`) | «Este anfitrión aún no puede recibir pagos en línea. Puedes apartar y pagar directo con él.» |
| B13 | Cupón expirado/agotado/excluido o doble descuento | `POST /coupons/validate`, `evaluate_coupon` | Vigencia, `max_redemptions`, `per_user_limit`, `excluded_venue_ids`, `first_booking_only`; `UNIQUE(coupon,user,booking)` | `test_cupon_no_baja_de_cero_ni_toca_la_garantia` + pruebas de cupón | «Este cupón alcanzó su límite de usos.» · «El cupón no aplica a este espacio.» |
| B14 | Depósito de garantía: retención, liberación, daños | `POST /bookings/{id}/check-out`, job `release_security_deposits` | Cobro separado `is_security_deposit`; liberación a las 48 h si no hay daños ni disputa; daños exigen evidencia | `test_state_machine.py` + job | «Depósito de garantía (reembolsable)» · «Para cobrar daños necesitas al menos una foto de evidencia.» |

## C. Identidad y confianza

| # | Caso | Endpoint | Validación | Test | Mensaje UX |
|---|---|---|---|---|---|
| C15 | Documentos falsificados o reutilizados | `POST /providers/me/verification-documents` | `file_sha256` comparado entre cuentas; `fraud_signals`; revisión manual | `test_documento_duplicado_entre_cuentas_dispara_revision` | «Revisamos tu expediente» (interno: bandera de fraude) |
| C16 | Suplantación, cuentas duplicadas, alias `+`, teléfonos reciclados | `POST /auth/register`, `PATCH /me` | `email_normalized` (colapsa `+` y googlemail) + `UNIQUE(phone_e164)`; cambio de teléfono exige reverificar | `test_auth_rbac.py` | «Ya existe una cuenta con este correo. Si es tuya, inicia sesión o recupera tu contraseña.» |
| C17 | Reseñas falsas o extorsivas | `POST /reviews`, `POST /reviews/{id}/report` | Sólo booking `COMPLETED`, ventana de 30 días, `review_risk()` (ráfaga, IP, lenguaje de extorsión) | `test_riesgo_de_resena_detecta_rafaga_y_extorsion` | «Sólo puedes reseñar espacios donde ya realizaste un evento.» |
| C18 | Menor de edad y T&C versionados | `POST /auth/register` | `birth_date` valida 18 años; `tos_version` se guarda y se versiona | `test_auth_rbac.py` | «Necesitas ser mayor de edad para reservar. Un tutor puede crear la cuenta.» |
| C19 | Bypass de plataforma | `POST /threads/{id}/messages` | `detect_contact()` con contexto anti-falsos-positivos; **advierte, no borra**; revisión humana antes de limitar | `test_detecta_contacto`, `test_no_marca_falsos_positivos`, `test_accion_escalona_y_no_bloquea_sola` | «Tu mensaje se envió, pero compartir datos de contacto fuera de la app quita la protección de pago y soporte.» |

## D. Contenido y catálogo

| # | Caso | Endpoint | Validación | Test | Mensaje UX |
|---|---|---|---|---|---|
| D20 | Fotos con derechos de autor, contenido inapropiado, geoetiquetas | `POST /venues/{id}/media`, moderación | `ModerationStatus.PENDING` por defecto; EXIF limpiado; reporte de terceros | `test_venues_search.py` (media) | «Tu foto está en revisión. Publicamos en cuanto la aprobemos.» |
| D21 | Dirección inexacta o sin geocodificación | `POST /venues`, `PATCH /venues/{id}` | Geocoder con circuit breaker; si falla, guarda `is_approximate=true` y permite pin manual | `test_venues_search.py` (geocode degradado) | «No pudimos ubicar la dirección exacta. Guarda el espacio y ajusta el pin en el mapa.» |
| D22 | Capacidad o amenidades falsas | `POST /venues/{id}/reports`, `trust_penalty()` | Reportes acumulan penalización de `trust_score` y afectan el orden del ranking | `test_penalizacion_de_confianza_tiene_tope` | «Gracias, vamos a verificar esa información con el anfitrión.» |
| D23 | Venue duplicado | `POST /admin/venues/merge` | `dedupe_hash` + revisión; merge mueve reservas y deja `merged_into_id`, todo auditado | `test_admin_audit.py` | (Interno) «Fusionados N espacios; X reservas movidas.» |
| D24 | Categoría/amenidad borrada en uso | `DELETE /admin/categories/{id}` | Soft-delete + migración a categoría por defecto (auditado con el conteo) | `test_admin_audit.py` | Sin impacto visible: el venue conserva una categoría válida |

## E. Disponibilidad

| # | Caso | Endpoint | Validación | Test | Mensaje UX |
|---|---|---|---|---|---|
| E25 | Reglas contradictorias (24 h con blackout parcial) | `PUT /venues/{id}/availability-rules` | Detección de solapes del mismo `slot_kind`/día; `crosses_midnight` coherente | `test_availability_rules_conflict` (en `test_venues_search.py`) | «Hay dos bloques del mismo tipo que se solapan el mismo día.» |
| E26 | Reservas solapadas por edición manual | Constraint de DB | `bookings_no_overlap` cubre `HOLD/PENDING_PAYMENT/CONFIRMED/COMPLETED` | `test_holds_solapados_son_imposibles_a_nivel_db` | El 409 explica el conflicto con la fuente (`hold`/`booking`/`blackout`) |
| E27 | Cambio de reglas afecta reservas futuras | `PATCH /venues/{id}`, blackouts | `quote_snapshot` + `rules_snapshot` en el booking ⇒ grandfathering; blackout con reservas exige `force` | `test_buffer_impide_eventos_pegados` + pruebas de snapshot | «Tu reserva conserva el precio y las condiciones del momento en que apartaste.» |
| E28 | Feriados y temporadas altas | `PUT /venues/{id}/pricing-rules`, `SeasonalRate` | Regla con `specific_dates` gana a la base; multiplicador de temporada | `test_prioridad_de_reglas_especificas_gana`, `test_temporada_alta_aplica_multiplicador` | «Temporada: Fin de año ×1.5» en el desglose (nunca oculto) |

## F. Operación y escalabilidad

| # | Caso | Endpoint / dónde | Validación | Test | Mensaje UX |
|---|---|---|---|---|---|
| F29 | Búsqueda geoespacial con radio grande | `GET /venues` | `radius_km ≤ 120`; GiST + trigram; `statement_timeout` de 8 s; caché 60 s | `test_venues_search.py` (radio y p95) + artillery | «El radio máximo de búsqueda es 120 km.» |
| F30 | Picos de tráfico | Middleware de rate limit | Cubetas por ruta/usuario, `X-RateLimit-*`, `Retry-After`; `adaptive_limit()` bajo carga | k6 + `test_rate_limit_headers` | «Demasiadas solicitudes. Espera un momento y vuelve a intentar.» |
| F31 | Caída de pagos/mapas/email | `/health`, `pybreaker`, Celery | Estado `degraded` visible; fallback a SPEI/pago directo; pin manual | `test_health_degraded` | «Un servicio externo está lento. Reintentamos automáticamente.» |
| F32 | Exportación/borrado (GDPR/LFPDPPP) sin romper contabilidad | `DELETE /me`, `GET /me/export` | Soft-delete + anonimización; se conservan `invoices/payments/payouts/ledger` con `retention_until` | `test_auth_rbac.py` (borrado) | «Tu cuenta se eliminó. Conservamos tus comprobantes fiscales de forma anonimizada por obligación legal.» |
| F33 | Accesibilidad (WCAG 2.2 AA / nativa) | Toda la app | Mínimo táctil 44 pt, `accessibilityLabel` con datos útiles, contraste ≥ 4.5:1, reduce-motion respetado | Revisión manual + `lighthouse` en la web pública | — |
| F34 | Multi-idioma/multi-moneda | Fase 3 | `currency` por venue + `locale` por usuario ya modelados; falta catálogo de traducciones | — | Fase 3 |

## Resumen por criticidad

| Nivel | Casos | Requisito de salida |
|---|---|---|
| Bloqueante para MVP | A1, A2, A5, A7, A8, B10, B13, B14, C16, C18, E26 | Test automatizado verde en CI + prueba manual del copy |
| Importante | A3, A4, A6, B9, B11, B12, C15, C17, C19, D21, E25, E27, E28, F29, F30, F31, F32 | Test verde; copy revisado por soporte |
| Deseable | D20, D22, D23, D24, F33 | Diseño documentado; prueba en el siguiente ciclo |
| Fase posterior | F34 | Modelado listo, implementación diferida |
