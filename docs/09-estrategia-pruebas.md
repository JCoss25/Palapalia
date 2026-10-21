# 09 · Estrategia de pruebas

## 9.1 Pirámide

| Nivel | Herramienta | Qué cubre | Tiempo objetivo |
|---|---|---|---|
| Unitarias de dominio | pytest | Máquina de estados, motor de precios, moderación, paginación, seguridad | < 5 s |
| Integración de API | pytest + httpx `AsyncClient` + Postgres/PostGIS real | Flujos completos, RBAC, idempotencia, constraints | < 90 s |
| Concurrencia y carga | k6 (o artillery) | Carrera por el mismo slot, picos, backpressure | < 2 min |
| E2E móvil | Maestro | Flujo crítico y estados de error en la app | < 3 min |
| Verificación de contrato | Schemathesis (opcional, CI nocturna) | `openapi.json` vs. implementación | — |

**Por qué Postgres real y no SQLite:** la garantía anti-doble-reserva es una `ExcludeConstraint`
con `tstzrange`. En SQLite esa prueba pasaría siempre y no probaría nada. Las unitarias de dominio
sí corren sin base de datos (`SimpleNamespace` como venue/regla), lo que las mantiene rápidas.

## 9.2 Estructura

```
backend/tests/
├─ conftest.py                 # engine por sesión, transacción por test, factorías, cliente async
├─ test_state_machine.py       # unitarias: transiciones válidas/prohibidas, guardas, actores
├─ test_pricing.py             # unitarias: desglose, DST, medianoche, cupones, política escalonada
├─ test_moderation.py          # unitarias: anti-bypass, falsos positivos, riesgo de reseñas
├─ test_bookings_flow.py       # integración: hold, idempotencia, carrera, buffer, cancelación
├─ test_payments_webhook.py    # integración: firma, duplicados, pago tardío, reembolsos
├─ test_venues_search.py       # (añadir) filtros, radio máximo, `is_available`, caché
├─ test_auth_rbac.py           # (añadir) registro, rotación de refresh, reuso, ban, permisos
├─ test_reviews_messaging.py   # (añadir) reseña post-evento, réplica, bypass, hilos
└─ test_admin_audit.py         # (añadir) impersonación, replay de webhooks, cadena de auditoría
e2e/
├─ k6/hold_race.js             # 25 usuarios al mismo slot: 1 éxito, 24 conflictos, 0 inesperados
└─ maestro/booking_flow.yaml   # onboarding → búsqueda → detalle → apartado → pago → reservas
```

## 9.3 Fixtures clave (`conftest.py`)

| Fixture | Qué hace |
|---|---|
| `engine` | Crea extensiones (`postgis`, `btree_gist`, `pg_trgm`) y el esquema una vez por sesión |
| `session` | Conexión + transacción por test con `begin_nested()`; se revierte al terminar |
| `client` | `AsyncClient` con `ASGITransport` y `get_db` sobreescrito a la sesión de prueba |
| `client_user` / `provider_user` | Usuarios con contraseña hasheada y TOS aceptados |
| `venue` | Venue publicado con 7 reglas de apertura, tarifa base, buffer de 60 min y garantía |
| `auth_headers` / `provider_headers` | JWT de acceso listos para usar |
| `future_window(days, hours)` | Ventana futura válida (respeta la anticipación mínima de 24 h) |

`TEST_DATABASE_URL` apunta a `quinta_test`. En CI: `docker compose up -d db` y
`alembic upgrade head` antes de pytest; alternativamente `testcontainers[postgres]` con la imagen
`postgis/postgis:16-3.4`.

## 9.4 Pruebas de concurrencia (k6)

`e2e/k6/hold_race.js` levanta 25 VUs que intentan apartar el mismo slot con la misma cuenta.
Umbrales (fallan el pipeline si no se cumplen):

| Métrica | Umbral | Por qué |
|---|---|---|
| `hold_created` | `count<=1` | Un solo ganador: invariante de negocio |
| `hold_conflict` | `count>=24` | El resto recibe 409 `VENUE_NOT_AVAILABLE` |
| `hold_unexpected` | `count==0` | Ni 500, ni 200 con folio duplicado |

```bash
VENUE_ID=<uuid> TOKEN=<access> RACERS=25 k6 run e2e/k6/hold_race.js
```

Complemento con artillery para el pico de lectura (`GET /venues` con geo) midiendo p95 < 400 ms y
verificando que el rate limit responde 429 con `Retry-After` en lugar de tumbar la API.

## 9.5 E2E móvil (Maestro)

`e2e/maestro/booking_flow.yaml` cubre:

1. Onboarding → buscador.
2. Selección de fecha y de venue (con verificación de la etiqueta `Patrocinado`).
3. Detalle → apartar (CTA con la fecha).
4. Checkout: folio, «Total hoy», reloj y método de pago.
5. Mis reservas: folio `QX-*` y acción siguiente.
6. **Casos de error**: `VENUE_NOT_AVAILABLE` («Se ocupó hace un momento» → «Elegir otro día») y
   `HOLD_EXPIRED` («Tu apartado venció» → «Volver a apartar»).

Se ejecutan con un mock de red para los estados de error (los flujos felices van contra staging).

## 9.6 Datos de prueba

| Necesidad | Estrategia |
|---|---|
| Venues plausibles | Factory con nombres reales del catálogo semilla (`seed.py`, pendiente) |
| Fechas | Siempre relativas a `now()`; nunca fechas fijas en el pasado |
| Fotos | Objetos en MinIO de pruebas; URLs firmadas |
| Pagos | `FakeGateway` con HMAC propio (ver `test_payments_webhook.py`), sin llamadas de red |
| Mapas | `maps.geocode` mockeado salvo en pruebas de degradación (circuito abierto) |
| Tiempo | `GuardContext(now=...)` permite probar «evento ya iniciado» sin esperar |

## 9.7 CI/CD

```yaml
# .github/workflows/ci.yml (resumen)
jobs:
  lint:      ruff check + ruff format --check + mypy --strict
  unit:      pytest -m "not integration"
  integration:
    services:  postgis/postgis:16-3.4 + redis:7
    steps:     alembic upgrade head && pytest -m integration
  openapi:   python -c "from app.main import app; app.openapi()" && git diff --exit-code openapi.json
  mobile:    npm ci && tsc --noEmit && eslint
  e2e:       maestro test e2e/maestro (sólo en main, contra staging)
```

Puertas de calidad: cobertura ≥ 80% en `app/services/*` (el dominio), 0 errores de `mypy --strict`,
0 `FAIL` del inspector de diseño en pantallas tocadas, y `openapi.json` sin cambios inesperados.
