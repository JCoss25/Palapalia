# Quinta — marketplace de palapas, quintas y salones para eventos

Monorepo del producto **Quinta**: reserva de espacios para eventos (palapas, salones de fiestas,
quintas, jardines y terrazas) con disponibilidad real, apartado con precio congelado, pagos con
split a proveedores, reseñas verificadas y mensajería.

```
palapa-market/
├─ docs/            especificación completa (empezar por docs/00-INDICE.md)
├─ backend/         FastAPI + SQLAlchemy 2.0 async + PostgreSQL/PostGIS + Celery
├─ web/             App web (Vite + React + TypeScript) — la que se abre en el navegador
├─ mobile/          React Native (Expo) + TypeScript — cliente móvil
├─ e2e/             Maestro (RN) y k6 (carga/concurrencia)
└─ .github/         CI: build de la web y despliegue a GitHub Pages
```

## Dos clientes, un contrato

La **app web** (`web/`) y la **app móvil** (`mobile/`) consumen la misma API y comparten el sistema
de diseño (paleta hueso/pino/verde agua, Fraunces + Inter, radios 2/4/16, un solo acento).

| Cliente | Stack | Cómo se abre |
|---|---|---|
| Web | Vite + React 18 + TS | `cd web && npm run dev` → http://127.0.0.1:5177/ |
| Web (un solo archivo) | build IIFE autocontenido | `cd web && npm run build:single` → abrir `web/dist-single/index.html` |
| Móvil | Expo + expo-router | `cd mobile && npm install && npx expo start` |

La web trae **respaldo local**: si la API no responde, funciona con un dataset de demostración y
calcula igual el anticipo, la comisión (7.5%), el IVA (16% sobre la comisión), el depósito de
garantía y la política de cancelación escalonada. Verás un aviso de "Modo demostración" cuando eso
ocurre. Para conectar la API real: `API_URL=http://127.0.0.1:8010 npm run dev`.


## Estado de los entregables

| # | Entregable | Archivo |
|---|---|---|
| 1 | Resumen ejecutivo, supuestos, ADRs | `docs/01-resumen-ejecutivo.md` |
| 2 | Arquitectura y modelo ER (Mermaid) | `docs/02-arquitectura.md` |
| 3 | OpenAPI 3.1 / contratos Pydantic | `docs/03-api-openapi.md` |
| 4 | Modelos SQLAlchemy async | `backend/app/models/*` |
| 5 | Máquina de estados de Booking | `docs/05-maquina-de-estados.md` |
| 6 | Idempotencia, webhooks y Celery | `docs/06-idempotencia-webhooks-colas.md` |
| 7 | Pantallas de referencia móvil | `docs/design/reference-screens.png` + `docs/07-pantallas-referencia.md` |
| 8 | Código React Native | `mobile/` |
| 9 | Estrategia de pruebas | `docs/09-estrategia-pruebas.md`, `backend/tests/`, `e2e/` |
| 10 | Edge cases → test | `docs/10-edge-cases.md` |
| 11 | Seguridad, privacidad y LFPDPPP | `docs/11-seguridad-privacidad.md` |
| 12 | Roadmap por fases | `docs/12-roadmap.md` |
| 13 | Checklist de aceptación | `docs/13-checklist-aceptacion.md` |
| — | Sistema de diseño (tokens, diales, reglas) | `docs/04-sistema-diseno.md` |

## Arranque rápido

```bash
# 1. Web (no necesita backend para revisarse)
cd web && npm install
npm run dev            # http://127.0.0.1:5177/
npm run build:single   # opcional: un HTML autocontenido, se abre con doble clic

# 2. Infra local (Postgres+PostGIS, Redis, Mailhog, MinIO)
cd ../backend && cp .env.example .env && docker compose up -d db redis minio mailhog

# 2. API
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --port 8000        # /docs · /redoc · /openapi.json

# 3. Workers
celery -A app.tasks.celery_app worker -l info -Q default,payments,notifications
celery -A app.tasks.celery_app beat -l info

# 4. Stripe (webhooks locales)
stripe listen --forward-to localhost:8000/api/v1/payments/webhook

# 5. Móvil
cd ../mobile && npm install && npx expo start
```

## Convenciones de la casa

- **Dinero**: enteros en centavos (`amount_cents`) + ISO-4217 (`currency`). Nunca `float`.
- **Tiempo**: `timestamptz` en UTC + `timezone` IANA del venue. La UI muestra hora local del venue.
- **IDs**: UUIDv7 expuestos como string; los IDs de ruta **nunca** se usan para autorizar.
- **Errores**: `{"error": {"code", "message", "details", "request_id"}}` con códigos estables.
- **Escrituras críticas**: `Idempotency-Key` obligatorio (bookings, payments, refunds, payouts).
- **Nada se borra**: `deleted_at` + `AuditLog`; retención fiscal aparte de la anonimización.
