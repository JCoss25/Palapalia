"""Migración inicial: extensiones, esquema completo y objetos SQL que el ORM no expresa.

**Por qué se crea desde el metadata.** En un esquema greenfield, la fuente de verdad es
`app/models/*` (incluye la `ExcludeConstraint` declarada con su cláusula `WHERE`). Crear las
tablas desde `Base.metadata` garantiza paridad modelo ↔ base de datos y evita dos definiciones
que se desincronizan. A partir de la segunda revisión, las migraciones son diffs escritos a mano
(o `--autogenerate` revisado) sobre este estado base.

Objetos que sí se escriben a mano aquí:
- `CREATE EXTENSION` de postgis, btree_gist, pg_trgm, pgcrypto
- trigger que mantiene `venues.search_vector` (tsvector en español con pesos A/B/C)
- índices de expresión que el ORM no genera (trigram ya está declarado en el modelo)

Revision ID: 0001_init
Revises: —
"""

from __future__ import annotations

from alembic import op

from app.db.base import Base
import app.models  # noqa: F401  (importa todos los modelos para poblar Base.metadata)

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

SEARCH_VECTOR_FUNCTION = """
CREATE OR REPLACE FUNCTION venues_search_vector_update() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('spanish', coalesce(NEW.name, '')), 'A') ||
        setweight(to_tsvector('spanish', coalesce(NEW.headline, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(NEW.city, '') || ' ' || coalesce(NEW.state, '')), 'B') ||
        setweight(to_tsvector('spanish', coalesce(NEW.description, '')), 'C');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

SEARCH_VECTOR_TRIGGER = """
CREATE TRIGGER trg_venues_search_vector
BEFORE INSERT OR UPDATE OF name, headline, city, state, description ON venues
FOR EACH ROW EXECUTE FUNCTION venues_search_vector_update();
"""


def upgrade() -> None:
    bind = op.get_bind()

    # ── Extensiones ───────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")   # requerido por EXCLUDE con venue_id =
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")      # búsqueda tolerante a errores de dedo
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")     # gen_random_uuid y hashing

    # ── Esquema completo desde los modelos ────────────────────────────────────
    # Incluye las constraints de exclusión (bookings_no_overlap, holds_no_overlap),
    # los índices GiST/GIN declarados y todas las constraints CHECK.
    Base.metadata.create_all(bind=bind, checkfirst=False)

    # ── Búsqueda de texto en venues ───────────────────────────────────────────
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.execute(SEARCH_VECTOR_TRIGGER)

    # ── Vistas materializadas de apoyo para el panel (se refrescan por job) ───
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS venue_occupancy_monthly AS
        SELECT
            b.venue_id,
            date_trunc('month', b.start_at AT TIME ZONE v.timezone)::date AS month,
            count(*) FILTER (WHERE b.state IN ('CONFIRMED','COMPLETED')) AS sold_events,
            count(*) FILTER (WHERE b.state IN ('CANCELLED_BY_CLIENT','CANCELLED_BY_PROVIDER')) AS cancelled_events,
            sum(b.paid_cents) FILTER (WHERE b.state IN ('CONFIRMED','COMPLETED')) AS revenue_cents
        FROM bookings b
        JOIN venues v ON v.id = b.venue_id
        WHERE b.deleted_at IS NULL
        GROUP BY 1, 2
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_venue_occupancy_monthly ON venue_occupancy_monthly (venue_id, month)"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS venue_occupancy_monthly")
    op.execute("DROP TRIGGER IF EXISTS trg_venues_search_vector ON venues")
    op.execute("DROP FUNCTION IF EXISTS venues_search_vector_update")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
