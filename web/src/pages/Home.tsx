/**
 * Home / búsqueda.
 *
 * Composición: la fecha es el primer control de la página (no un filtro escondido en un panel).
 * Debajo, tres módulos con ritmos distintos —rejilla de tarjetas, lista compacta y un bloque
 * editorial con foto— porque repetir la misma tarjeta tres veces es el tell más común.
 */

import { useMemo, useState } from 'react';

import { Chip, DateStrip, VenueCard, VenueRow } from '../components/Catalog';
import { Reveal, SectionHead } from '../components/Layout';
import { CATEGORIES, EVENT_TYPES, availabilityFor } from '../data/demo';
import { searchVenues } from '../data/source';
import { fmt, money } from '../lib/format';
import { navigate } from '../lib/router';
import { useAsync } from '../lib/useAsync';

export function HomePage() {
  const [selectedDate, setSelectedDate] = useState(() => {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    date.setHours(0, 0, 0, 0);
    return date;
  });
  const [category, setCategory] = useState<string>('Todas');
  const [eventType, setEventType] = useState<string | null>(null);
  const [guests, setGuests] = useState(40);
  const [query, setQuery] = useState('');

  const dateFrom = fmt.isoDate(selectedDate);
  const dateTo = fmt.isoDate(new Date(selectedDate.getTime() + 86400000));

  const search = useAsync(
    () => searchVenues({ dateFrom, dateTo, guests, query: query.trim() || undefined }),
    [dateFrom, dateTo, guests, query],
  );

  const results = useMemo(() => {
    const list = search.data?.venues ?? [];
    return list
      .filter((venue) => {
        if (category !== 'Todas' && venue.category !== category) return false;
        if (eventType && venue.eventTypes.length > 0 && !venue.eventTypes.includes(eventType)) return false;
        if (venue.capacityMax < guests) return false;
        return true;
      })
      .map((venue) => ({
        venue,
        isAvailable:
          venue.availableOnDate ??
          availabilityFor(venue, selectedDate, 1)[0]?.isAvailable ??
          false,
      }));
  }, [category, eventType, guests, search.data, selectedDate]);

  const available = results.filter((item) => item.isAvailable);
  const busy = results.filter((item) => !item.isAvailable);
  const [primary, ...rest] = available;
  const cheapest = [...available].sort((a, b) => a.venue.basePriceCents - b.venue.basePriceCents)[0];
  const fromApi = !search.fromFallback;

  return (
    <main id="contenido">
      {/* ── Primera pantalla: la fecha manda ─────────────────────────────── */}
      <section className="section-tight">
        <div className="wrap stack-lg">
          <div className="stack">
            <h1 className="display display-xl">
              La fecha
              <br />
              va primero.
            </h1>
            <p className="lead">
              Palapas, quintas y salones con disponibilidad real. Aparta tu día y el precio queda congelado
              mientras pagas el anticipo.
            </p>
          </div>

          <div className="stack" style={{ gap: 'var(--s-3)' }}>
            <p className="caption" id="fecha-label">
              Elige el día de tu evento
            </p>
            <div aria-labelledby="fecha-label">
              <DateStrip selected={selectedDate} onSelect={setSelectedDate} />
            </div>
          </div>

          <div
            style={{
              display: 'grid',
              gap: 'var(--s-3)',
              gridTemplateColumns: 'minmax(0, 1fr)',
            }}
          >
            <div className="field">
              <label htmlFor="buscar">Busca por nombre, ciudad o tipo de espacio</label>
              <input
                id="buscar"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Palapa en Telchac, quinta en Querétaro…"
                autoComplete="off"
              />
            </div>
          </div>

          <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--s-2)' }}>
            {CATEGORIES.map((item) => (
              <Chip
                key={item}
                label={item}
                selected={category === item}
                onClick={() => setCategory(item)}
              />
            ))}
          </div>

          <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--s-2)' }}>
            <span className="caption" style={{ marginRight: 'var(--s-1)' }}>
              Tipo de evento
            </span>
            {EVENT_TYPES.map((item) => (
              <Chip
                key={item}
                label={item}
                selected={eventType === item}
                onClick={() => setEventType(eventType === item ? null : item)}
              />
            ))}
          </div>

          <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--s-2)' }}>
            <span className="caption" style={{ marginRight: 'var(--s-1)' }}>
              Invitados
            </span>
            {[40, 80, 120, 200, 250].map((value) => (
              <Chip
                key={value}
                label={`${value}`}
                selected={guests === value}
                onClick={() => setGuests(value)}
              />
            ))}
          </div>
        </div>
      </section>

      {/* ── Resultados ───────────────────────────────────────────────────── */}
      <section className="section-tight">
        <div className="wrap">
          <SectionHead
            title={`${available.length} ${available.length === 1 ? 'espacio libre' : 'espacios libres'} el ${fmt.dayLong(selectedDate)}`}
            note={
              search.loading
                ? 'Consultando disponibilidad…'
                : cheapest
                  ? `El más económico de ese día: ${cheapest.venue.name}, desde ${money(cheapest.venue.basePriceCents)} por día.${fromApi ? '' : ' Datos de demostración.'}`
                  : 'Ajusta la fecha, el número de invitados o quita algún filtro.'
            }
          />

          {primary ? (
            <div className="grid-cards">
              <Reveal>
                <VenueCard
                  priority
                  venue={primary.venue}
                  isAvailable={primary.isAvailable}
                  onOpen={() => navigate(`/espacio/${primary.venue.id}?fecha=${fmt.isoDate(selectedDate)}`)}
                />
              </Reveal>
              {rest.slice(0, 5).map((item, index) => (
                <Reveal key={item.venue.id} delay={(index + 1) * 70}>
                  <VenueCard
                    priority={index === 0}
                    venue={item.venue}
                    isAvailable={item.isAvailable}
                    onOpen={() => navigate(`/espacio/${item.venue.id}?fecha=${fmt.isoDate(selectedDate)}`)}
                  />
                </Reveal>
              ))}
            </div>
          ) : (
            <div className="surface" style={{ padding: 'var(--s-6)' }}>
              <h3 className="display display-md">Nada libre el {fmt.dayLong(selectedDate)}</h3>
              <p className="body-soft measure" style={{ marginTop: 'var(--s-3)' }}>
                Los espacios de tu filtro están ocupados o necesitan más anticipación. Prueba otro día: la
                mayoría de los anfitriones abre fechas con dos semanas de aviso.
              </p>
              <div className="row" style={{ marginTop: 'var(--s-5)', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    const next = new Date(selectedDate);
                    next.setDate(next.getDate() + 1);
                    setSelectedDate(next);
                  }}
                >
                  Ver el día siguiente
                </button>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => {
                    setCategory('Todas');
                    setEventType(null);
                    setGuests(40);
                    setQuery('');
                  }}
                >
                  Quitar filtros
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ── Segunda composición: lista compacta ──────────────────────────── */}
      {busy.length > 0 ? (
        <section className="section-tight">
          <div className="wrap">
            <SectionHead
              title="Ocupados ese día, pero con fechas cercanas"
              note="Los mostramos porque suelen liberar fechas: si cancelan, el calendario se abre solo."
            />
            <div
              style={{
                display: 'grid',
                gap: 'var(--s-5) var(--s-8)',
                gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
              }}
            >
              {busy.slice(0, 4).map((item) => (
                <VenueRow
                  key={item.venue.id}
                  venue={item.venue}
                  onOpen={() => navigate(`/espacio/${item.venue.id}`)}
                />
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {/* ── Bloque editorial con foto: lo que cambia el resultado ────────── */}
      <section className="section">
        <div className="wrap split">
          <div className="stack-lg">
            <h2 className="display display-lg">
              Lo que casi nadie
              <br />
              te dice al rentar.
            </h2>
            <ol
              className="stack-lg"
              style={{ listStyle: 'none', padding: 0, margin: 0 }}
              aria-label="Qué revisar antes de apartar"
            >
              <li className="stack" style={{ gap: 'var(--s-2)' }}>
                <h3 className="display display-md">El tiempo de limpieza se cobra en el calendario</h3>
                <p className="body-soft measure">
                  Un evento que termina a las 02:00 no permite otro que empiece a las 08:00. Aquí el buffer de
                  limpieza se bloquea al apartar, así que no hay sorpresas ni discusiones con el anfitrión.
                </p>
              </li>
              <li className="stack" style={{ gap: 'var(--s-2)' }}>
                <h3 className="display display-md">El precio del apartado no se mueve</h3>
                <p className="body-soft measure">
                  Si el anfitrión sube la tarifa para temporada alta mientras pagas, tu reserva conserva el
                  precio del momento en que apartaste. Se guarda el desglose completo, no un total suelto.
                </p>
              </li>
              <li className="stack" style={{ gap: 'var(--s-2)' }}>
                <h3 className="display display-md">El depósito de garantía no es un cargo</h3>
                <p className="body-soft measure">
                  Se retiene aparte del total y se libera 48 horas después del evento si no hay daños
                  documentados con evidencia. Está separado en el recibo para que no se confunda con el pago.
                </p>
              </li>
            </ol>
          </div>
          <Reveal delay={80}>
            <figure style={{ margin: 0 }}>
              <span className="media media-4x3">
                <img
                  src="/images/palma-textura.jpg"
                  alt="Tejido de hojas de palma seca a contraluz, material del techo de una palapa"
                  loading="lazy"
                  decoding="async"
                />
              </span>
              <figcaption className="caption" style={{ marginTop: 'var(--s-3)' }}>
                Techo de palma de una palapa en Telchac Puerto, Yucatán. Se reemplaza cada tres años: es lo
                primero que se revisa en una inspección.
              </figcaption>
            </figure>
          </Reveal>
        </div>
      </section>

      {/* ── Cómo funciona, con <ol> real porque el orden importa ─────────── */}
      <section className="section-tone tone-bone section-tight">
        <div className="wrap">
          <SectionHead title="Cómo se aparta" note="Tres pasos y quince minutos, sin llamadas." />
          <ol
            style={{
              display: 'grid',
              gap: 'var(--s-6)',
              gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
              listStyle: 'none',
              padding: 0,
              margin: 0,
            }}
          >
            <li className="surface" style={{ padding: 'var(--s-5)' }}>
              <h3 style={{ fontSize: '1.0625rem' }}>Apartas y el precio se congela</h3>
              <p className="body-soft" style={{ marginTop: 'var(--s-2)' }}>
                Recibes un folio y un reloj de 15 minutos. Ese horario queda bloqueado para nadie más mientras
                pagas.
              </p>
            </li>
            <li className="surface" style={{ padding: 'var(--s-5)' }}>
              <h3 style={{ fontSize: '1.0625rem' }}>Pagas el 30% de anticipo</h3>              <p className="body-soft" style={{ marginTop: 'var(--s-2)' }}>
                Con tarjeta, transferencia o directo al anfitrión. El saldo se cobra antes del evento y el
                depósito de garantía va aparte.
              </p>
            </li>
            <li className="surface" style={{ padding: 'var(--s-5)' }}>
              <h3 style={{ fontSize: '1.0625rem' }}>Recibes contrato y recordatorios</h3>
              <p className="body-soft" style={{ marginTop: 'var(--s-2)' }}>
                El contrato se genera con el desglose congelado y te avisamos 24 horas y 2 horas antes.
              </p>
            </li>
          </ol>
        </div>
      </section>
    </main>
  );
}
