# 12 · Roadmap por fases

## Fase 1 · MVP — «buscar, apartar y reseñar» (10–12 semanas)

**Objetivo de negocio:** que una persona encuentre un espacio libre en su fecha, lo aparte y pague
el anticipo sin hablar con nadie; y que el anfitrión pueda publicar y ver su agenda.

| Módulo | Alcance | Criterio de salida |
|---|---|---|
| Auth | Registro cliente/proveedor, login, refresh rotado, verificación de correo y OTP de teléfono | Un usuario nuevo completa registro y verificación sin soporte |
| Proveedores | Solicitud, verificación documental (INE, domicilio, CSF, escritura), aprobación admin | 5 proveedores reales aprobados con expediente completo |
| Venues | CRUD, media con URL firmada, categorías/amenidades/tipos, geocodificación con pin manual | 30 espacios publicados con ≥ 6 fotos cada uno |
| Disponibilidad | Reglas semanales, blackouts, buffers, cotización con snapshot | `quote` determinista; 0 conflictos de reglas al guardar |
| Reservas | Hold con TTL, conversión a booking, contrato PDF, cancelación escalonada, reprogramación | Carrera k6 con 1 ganador y 24 conflictos; 0 solapes |
| Pagos | Stripe Connect, anticipo, webhook firmado, reembolsos, payout manual por admin | Pago real de punta a punta en staging con reembolso verificado |
| Reseñas | Post-evento con ventana de 30 días, réplica del anfitrión, reportes | ≥ 20 reseñas verificadas |
| Notificaciones | Push + email, recordatorios 24 h/2 h, expiración de holds | Entrega > 98% de recordatorios |
| Panel proveedor | Ocupación, ingresos, agenda, tareas pendientes | Un anfitrión gestiona un mes completo sin soporte |
| Móvil | 6 pantallas: bienvenida, explorar, detalle, apartado, reservas, panel | Maestro verde en CI |
| Infra | Docker, entornos dev/staging/prod, Alembic, CI con pytest + k6 | Despliegue reproducible en < 15 min |

**Fuera del MVP:** mensajería completa (sólo consulta básica), CFDI automático, disputas complejas,
API pública, multi-moneda.

## Fase 2 · «cobrar bien y defender el dinero» (8 semanas)

| Módulo | Alcance | Criterio de salida |
|---|---|---|
| Pagos | Saldo programado (cobro automático X días antes), SPEI con conciliación, payouts automáticos semanales | 100% de payouts liberados sin intervención manual |
| Disputas | Evidencias, plazos del banco, resolución con reparto, congelamiento y descongelamiento | Disputas resueltas en < 7 días |
| CFDI 4.0 | Timbrado automático con PAC, cancelación con motivo SAT, envío por correo | 100% de facturas solicitadas timbradas en < 5 min |
| Cupones y promociones | CRUD admin, validación con restricciones, campañas por categoría | Cupón aplicado correctamente en 3 escenarios de exclusión |
| Operación del día | Check-in con QR, check-out con evidencia, depósito de garantía con daños | 20 eventos operados con check-in digital |
| Mensajería | Hilos por reserva, adjuntos, moderación con revisión humana, WebSocket estable | Latencia de mensaje < 500 ms |
| Web pública (SEO) | Páginas de venue y ciudad, datos estructurados, sitemap | 30 páginas indexadas, Lighthouse ≥ 90 en SEO |
| Datos | Exportación ARCO automatizada, panel de métricas admin | Reporte de retención y borrado auditado |

## Fase 3 · «escalar y abrir» (12 semanas)

| Módulo | Alcance |
|---|---|
| API pública | Tokens por proveedor, webhooks documentados, SDK ligero, sandbox |
| Multi-moneda e idioma | `currency` por venue, catálogo es/en, formatos por locale |
| Búsqueda avanzada | OpenSearch si el catálogo supera ~500k venues; filtros por temporada y clima |
| Recomendación | Ranking con señales de calidad (respuesta, cancelaciones, exactitud del anuncio) |
| App de staff | Check-in en puerta, lista de invitados, control de aforo |
| Pagos alternos | MercadoPago en producción, meses sin intereses, OXXO |
| Analítica de negocio | Cohortes de conversión, embudo hold→pago→completado, churn de proveedores |

## Dependencias críticas y riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Stripe Connect tarda en habilitar la cuenta MX | Media | Alto | Adaptador de MercadoPago listo; modo `offline_booking_only` |
| Geocodificación imprecisa en zonas rurales | Alta | Medio | Pin manual + `is_approximate` visible; validación de soporte |
| Baja liquidez inicial (pocos venues por ciudad) | Alta | Alto | Lanzamiento ciudad por ciudad (Progreso/Mérida → Querétaro → Guadalajara) |
| Fraude en documentos | Media | Alto | Revisión manual + señales de hash duplicado + suspensión rápida |
| Bypass de plataforma | Alta | Medio | Advertencia en mensajes + valor (contrato, garantía, soporte) en vez de bloqueo ciego |
| Carga tributaria del modelo de intermediación | Media | Alto | Validar con contador antes de F2 (CFDI y retenciones) |

## Métricas por fase

| Fase | Métrica norte | Umbral de avance |
|---|---|---|
| F1 | Reservas confirmadas / semana | 25 en la ciudad piloto, conversión hold→pago > 45% |
| F2 | GMV / mes | 1.2 M MXN, tasa de disputa < 1.5% |
| F3 | Proveedores activos por ciudad | 60, ocupación de fin de semana > 55% |
