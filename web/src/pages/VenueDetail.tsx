/**
 * Detalle del espacio.
 *
 * La disponibilidad es el bloque principal, no una pestaña. El CTA nombra la fecha:
 * no existen dos reservas iguales, así que el botón no dice «Reservar».
 */

import { useMemo, useState } from 'react';

import { AvailabilityGrid, PolicyNote } from '../components/Catalog';
import { Reveal, SectionHead, Stars } from '../components/Layout';
import { availabilityFor, refundTier, type Quote } from '../data/demo';
import { getAvailability, getQuote, getVenue, type DayAvailability } from '../data/source';
import { fmt, money } from '../lib/format';
import { navigate } from '../lib/router';
import { useAsync } from '../lib/useAsync';

export function VenueDetailPage({ venueId, initialDate }: { venueId: string; initialDate?: string }) {
  const [selectedDate, setSelectedDate] = useState(() => {
    if (initialDate) {
      const parsed = new Date(`${initialDate}T12:00:00`);
      if (!Number.isNaN(parsed.getTime())) return parsed;
    }
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    return tomorrow;
  });
  const [guests, setGuests] = useState(80);
  const [activeImage, setActiveImage] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Los hooks van antes de cualquier retorno anticipado: la API primero, el respaldo local después.
  const venueQuery = useAsync(() => getVenue(venueId), [venueId]);
  const venue = venueQuery.data;
  const selectedIso = fmt.isoDate(selectedDate);

  const availabilityQuery = useAsync(
    () =>
      venue
        ? getAvailability(venue, selectedDate, 14)
        : Promise.resolve({ data: [] as DayAvailability[], fromFallback: true }),
    [venue?.id, selectedIso],
  );

  const window = useMemo(() => {
    const start = new Date(selectedDate);
    start.setHours(15, 0, 0, 0);
    const end = new Date(start);
    end.setHours(end.getHours() + 11);
    return { start, end };
  }, [selectedDate]);

  const quoteQuery = useAsync(
    () =>
      venue
        ? getQuote(venue, window.start, window.end, guests)
        : Promise.resolve({ data: null as Quote | null, fromFallback: true }),
    [venue?.id, window.start.toISOString(), guests],
  );

  const quote = quoteQuery.data;

  const dayInfo = useMemo(() => {
    const fromApi = availabilityQuery.data?.find((day) => day.date === selectedIso);
    if (fromApi) return fromApi;
    if (!venue) return { date: selectedIso, isAvailable: false, reason: 'past' as string | null };
    const local = availabilityFor(venue, selectedDate, 1)[0]!;
    return { date: selectedIso, isAvailable: local.isAvailable, reason: local.reason as string | null };
  }, [availabilityQuery.data, selectedDate, selectedIso, venue]);

  if (venueQuery.loading) {
    return (
      <main id="contenido" className="wrap section">
        <p className="caption">Cargando el espacio…</p>
      </main>
    );
  }

  if (!venue) {
    return (
      <main id="contenido" className="wrap section">
        <h1 className="display display-lg">Ese espacio ya no está publicado</h1>
        <p className="body-soft measure" style={{ marginTop: 'var(--s-3)' }}>
          Puede que el anfitrión lo haya pausado. El catálogo se actualiza al instante: vuelve al buscador y
          elige otro.
        </p>
        <button type="button" className="btn" style={{ marginTop: 'var(--s-5)' }} onClick={() => navigate('/')}>
          Volver al buscador
        </button>
      </main>
    );
  }

  const daysUntilEvent = Math.round((window.start.getTime() - Date.now()) / 86400000);
  const tier = refundTier(daysUntilEvent);

  const handleHold = () => {
    if (!dayInfo.isAvailable) {
      setError(
        dayInfo.reason === 'min_notice'
          ? `Este espacio necesita al menos ${venue.minNoticeHours} horas de anticipación. Elige una fecha más adelante.`
          : 'Ese día ya está ocupado. Las fechas contiguas respetan el tiempo de limpieza entre eventos.',
      );
      return;
    }
    navigate(
      `/apartar/${venue.id}?inicio=${encodeURIComponent(window.start.toISOString())}&fin=${encodeURIComponent(
        window.end.toISOString(),
      )}&invitados=${guests}`,
    );
  };

  const cover = venue.images[activeImage] ?? venue.images[0]!;

  return (
    <main id="contenido">
      <section className="wrap" style={{ paddingTop: 'var(--s-5)' }}>
        <nav aria-label="Ruta" className="caption" style={{ marginBottom: 'var(--s-4)' }}>
          <a
            href="#/"
            onClick={(event) => {
              event.preventDefault();
              navigate('/');
            }}
            style={{ color: 'var(--sea)', fontWeight: 600 }}
          >
            Explorar
          </a>
          <span> · {venue.category} en {venue.city}</span>
        </nav>

        <div className="split">
          {/* ── Columna principal ───────────────────────────────────────── */}
          <div className="stack-lg">
            <div className="stack" style={{ gap: 'var(--s-3)' }}>
              <h1 className="display display-lg">{venue.name}</h1>
              <div className="row" style={{ flexWrap: 'wrap', gap: 'var(--s-3)' }}>
                <Stars rating={venue.rating} reviews={venue.reviews} />
                <span className="caption">
                  {venue.city}, {venue.state} · hasta {venue.capacityMax} personas · renta mínima de{' '}
                  {venue.minHours} h
                </span>
              </div>
              <p className="lead">{venue.headline}</p>
            </div>

            <Reveal>
              <div className="stack" style={{ gap: 'var(--s-2)' }}>
                <span className="media media-16x10">
                  <img src={cover.src} alt={cover.alt} />
                </span>
                <div className="row" style={{ gap: 'var(--s-2)' }}>
                  {venue.images.map((image, index) => (
                    <button
                      key={image.src}
                      type="button"
                      onClick={() => setActiveImage(index)}
                      aria-label={`Ver foto ${index + 1} de ${venue.images.length}`}
                      aria-pressed={index === activeImage}
                      className="media"
                      style={{
                        width: 72,
                        height: 72,
                        padding: 0,
                        cursor: 'pointer',
                        border: index === activeImage ? '2px solid var(--sea)' : '1px solid var(--line)',
                      }}
                    >
                      <img src={image.src} alt="" />
                    </button>
                  ))}
                </div>
              </div>
            </Reveal>

            <div className="stack">
              <h2 className="display display-md">El espacio</h2>
              <p className="body-soft measure">{venue.description}</p>
            </div>

            <div className="stack">
              <h2 className="display display-md">Qué incluye</h2>
              <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {venue.includes.map((item) => (
                  <li key={item} className="row" style={{ alignItems: 'flex-start', gap: 'var(--s-3)' }}>
                    <span aria-hidden="true" style={{ color: 'var(--sea)', fontWeight: 600 }}>
                      ✓
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="stack">
              <h2 className="display display-md">Servicios del espacio</h2>
              <ul className="row" style={{ flexWrap: 'wrap', gap: 'var(--s-2)', listStyle: 'none', padding: 0, margin: 0 }}>
                {venue.amenities.map((item) => (
                  <li
                    key={item}
                    style={{
                      background: 'var(--bone)',
                      borderRadius: 'var(--r-control)',
                      padding: '6px var(--s-3)',
                      fontSize: '0.875rem',
                    }}
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>

            <div className="stack">
              <h2 className="display display-md">Reglas de la casa</h2>
              <p className="body-soft measure">{venue.houseRules}</p>
            </div>

            <div className="stack">
              <h2 className="display display-md">Qué pasa si cancelas</h2>
              <p className="body-soft measure">
                Hoy faltan {daysUntilEvent} días para el evento: {tier.explanation} El depósito de garantía de{' '}
                {money(venue.securityDepositCents)} se libera 48 horas después del evento si no hay daños
                documentados con evidencia.
              </p>
            </div>
          </div>

          {/* ── Aside pegajoso: apartar ─────────────────────────────────── */}
          <aside className="sticky-aside stack" style={{ gap: 'var(--s-4)' }}>
            <div className="surface stack" style={{ padding: 'var(--s-5)', gap: 'var(--s-4)' }}>
              <div className="row-between" style={{ alignItems: 'baseline' }}>
                <div>
                  <p className="caption">Total del evento</p>
                  <p style={{ fontSize: '1.75rem', fontWeight: 600, letterSpacing: '-0.02em' }}>
                    {quote ? money(quote.totalCents) : '—'}
                  </p>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <p className="caption">Anticipo hoy</p>
                  <p style={{ fontWeight: 600 }}>{quote ? money(quote.depositCents) : '—'}</p>
                </div>
              </div>

              <div className="stack" style={{ gap: 'var(--s-2)' }}>
                <p className="caption" id="disp-label">
                  Disponibilidad de {fmt.monthYear(selectedDate)}
                </p>
                <div aria-labelledby="disp-label">
                  <AvailabilityGrid venue={venue} selectedDate={selectedDate} onSelect={setSelectedDate} />
                </div>
                {!dayInfo.isAvailable ? (
                  <p className="caption" style={{ color: 'var(--danger)' }}>
                    {dayInfo.reason === 'min_notice'
                      ? `Ese día no cumple la anticipación mínima de ${venue.minNoticeHours} h.`
                      : 'Ese día ya está ocupado.'}
                  </p>
                ) : (
                  <p className="available">
                    {fmt.dayLong(selectedDate)} está libre · {venue.minHours} h mínimo
                  </p>
                )}
              </div>

              <div className="stack" style={{ gap: 'var(--s-2)' }}>
                <label className="caption" htmlFor="invitados">
                  Invitados: {guests}
                </label>
                <input
                  id="invitados"
                  type="range"
                  min={venue.capacityMin}
                  max={venue.capacityMax}
                  step={5}
                  value={guests}
                  onChange={(event) => setGuests(Number(event.target.value))}
                />
                <p className="caption">
                  Entre {venue.capacityMin} y {venue.capacityMax} personas. Los invitados adicionales sobre{' '}
                  {venue.extraGuestFrom} cuestan {money(venue.extraGuestCents)} cada uno.
                </p>
              </div>

              <button type="button" className="btn btn-block" onClick={handleHold}>
                Apartar el {fmt.dateShort(selectedDate)}
                {quote ? ` · ${money(quote.depositCents)}` : ''}
              </button>
              <p className="caption">
                El apartado bloquea el horario 15 minutos y congela este desglose. No se cobra nada hasta que
                pagues.
              </p>
            </div>

            {error ? (
              <div className="notice notice-warning" role="alert">
                <strong>Esa fecha no se puede apartar</strong>
                <p>{error}</p>
                <button
                  type="button"
                  className="btn btn-quiet btn-sm"
                  onClick={() => {
                    const next = new Date(selectedDate);
                    next.setDate(next.getDate() + 1);
                    setSelectedDate(next);
                    setError(null);
                  }}
                >
                  Ver el día siguiente
                </button>
              </div>
            ) : null}

            <div className="surface stack" style={{ padding: 'var(--s-5)', gap: 'var(--s-3)' }}>
              <SectionHead title="Desglose" />
              {(quote?.lines ?? []).map((line) => (
                <div key={`${line.concept}-${line.amountCents}`} className="line">
                  <span className={`line-label ${line.isDiscount ? 'line-discount' : ''}`.trim()}>
                    {line.concept}
                    {line.detail ? ` · ${line.detail}` : ''}
                  </span>
                  <span className={`num ${line.isDiscount ? 'line-discount' : ''}`.trim()}>
                    {line.isDiscount ? '−' : ''}
                    {money(line.amountCents)}
                  </span>
                </div>
              ))}
            </div>

            <PolicyNote
              text={`Cancelación escalonada: 100% hasta 30 días antes, 50% entre 7 y 30 días. Después de 7 días no hay reembolso del anticipo. El anticipo del ${venue.depositPercent}% congela el precio mientras el apartado esté activo.`}
            />
          </aside>
        </div>
      </section>
    </main>
  );
}
