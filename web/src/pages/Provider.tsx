/**
 * Panel del anfitrión.
 *
 * Números reales, no gráficas decorativas: ocupación con los días marcados y la próxima
 * cobranza. El bloqueo de verificación aparece antes de que duela.
 */

import { Reveal, SectionHead } from '../components/Layout';
import { getProviderDashboard } from '../data/source';
import { VENUES } from '../data/demo';
import { fmt, money } from '../lib/format';
import { navigate } from '../lib/router';
import { useAsync } from '../lib/useAsync';

const WEEKDAYS = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];

export function ProviderPage() {
  const dashboardQuery = useAsync(() => getProviderDashboard(), []);
  const data = dashboardQuery.data;

  if (!data) {
    return (
      <main id="contenido" className="wrap section">
        <p className="caption">
          {dashboardQuery.error ? 'No pudimos cargar el panel. Reintenta en un momento.' : 'Cargando el panel…'}
        </p>
      </main>
    );
  }

  const occupancy = Math.round(data.occupancyRate * 100);

  const nextEvents = data.events.map((event) => {
    const date = new Date();
    date.setDate(date.getDate() + event.dayOffset);
    return { ...event, date };
  });

  return (
    <main id="contenido" className="section-tight">
      <div className="wrap">
        <div className="stack" style={{ gap: 'var(--s-2)', marginBottom: 'var(--s-5)' }}>
          <p className="caption">Panel · {data.providerName} · {data.period}</p>
          <h1 className="display display-lg">
            {occupancy >= 70 ? (
              <>
                El mes
                <br />
                va lleno.
              </>
            ) : (
              <>
                Aún hay
                <br />
                fechas libres.
              </>
            )}
          </h1>
        </div>

        {data.pendingTasks.length > 0 ? (
          <div className="stack" style={{ gap: 'var(--s-3)', marginBottom: 'var(--s-6)' }}>
            {data.pendingTasks.map((task) => (
              <div
                key={task.kind}
                className={`notice ${task.severity === 'action' ? 'notice-warning' : 'notice-info'}`.trim()}
              >
                <strong>{task.label}</strong>
                <p>
                  {task.kind === 'verification'
                    ? 'Sin la constancia de situación fiscal no podemos timbrar tus facturas. Puedes seguir recibiendo reservas y pagos.'
                    : 'Responder en menos de 4 horas mejora tu posición en la búsqueda.'}
                </p>
                {task.severity === 'action' ? (
                  <button type="button" className="btn btn-quiet btn-sm" style={{ justifySelf: 'start' }}>
                    Responder ahora
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}

        <div className="kpi-grid">
          <div className="kpi">
            <p className="caption">Ocupación del periodo</p>
            <b>{occupancy}%</b>
            <p className="caption">{data.upcomingEvents} eventos por delante</p>
          </div>
          <div className="kpi">
            <p className="caption">Ingreso del periodo</p>
            <b>{money(data.revenueCents)}</b>
            <p className="caption">
              {data.confirmedBookings} reservas · ticket promedio {money(data.avgTicketCents)}
            </p>
          </div>
          <div className="kpi">
            <p className="caption">Calificación</p>
            <b>{data.rating.toFixed(1)}</b>
            <p className="caption">de 5, con reseñas verificadas</p>
          </div>
          <div className="kpi">
            <p className="caption">Próxima liquidación</p>
            <b>{money(data.nextPayoutCents)}</b>
            <p className="caption">
              se libera el {fmt.dateShort(new Date(`${data.nextPayoutDate}T12:00:00`))} si no hay disputas
            </p>
          </div>
        </div>

        <section className="section-tight">
          <SectionHead
            title="Días apartados"
            note={`${data.cancellations} cancelación en el periodo. Las barras verdes son días vendidos.`}
          />
          <Reveal>
            <div>
              <div className="bars">
                {data.occupancyByWeekday.map((value, index) => (
                  <span
                    key={`${WEEKDAYS[index]}-${index}`}
                    style={{ height: `${Math.max(value, 0.04) * 100}%` }}
                    data-high={value >= 0.75 ? 'true' : 'false'}
                    title={`${WEEKDAYS[index]}: ${Math.round(value * 100)}% ocupado`}
                  />
                ))}
              </div>
              <div className="bar-labels">
                {WEEKDAYS.map((label, index) => (
                  <span key={`${label}-label-${index}`}>{label}</span>
                ))}
              </div>
              <p className="caption" style={{ marginTop: 'var(--s-3)' }}>
                El sábado es el día más vendido y el lunes el más flojo. Publicar una tarifa de lunes a
                miércoles suele llenar el hueco sin tocar el precio del fin de semana.
              </p>
            </div>
          </Reveal>
        </section>

        <section className="section-tight">
          <SectionHead title="Próximos eventos" note="Con el estado del cobro de cada uno." />
          <div className="hairline-list">
            {nextEvents.map((event, index) => (
              <Reveal key={event.folio} delay={index * 60}>
                <div className="history-row">
                  <span className="grow">
                    <p style={{ fontWeight: 600 }}>{event.kind}</p>
                    <p className="caption">
                      {fmt.dayLong(event.date)} · {event.guests} personas · folio {event.folio}
                    </p>
                  </span>
                  <span style={{ textAlign: 'right' }}>
                    <span style={{ fontWeight: 600, display: 'block' }}>{money(event.amountCents)}</span>
                    <span className="caption">{event.status}</span>
                  </span>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        <section className="section-tight">
          <SectionHead title="Tus espacios" note="Publica, pausa o ajusta precios sin perder reservas vivas." />
          <div className="grid-cards">
            {VENUES.slice(0, 3).map((venue, index) => (
              <Reveal key={venue.id} delay={index * 70}>
                <article className="surface stack" style={{ padding: 'var(--s-4)', gap: 'var(--s-3)' }}>
                  <span className="media media-16x10">
                    <img src={venue.images[0]!.src} alt={venue.images[0]!.alt} loading="lazy" />
                  </span>
                  <div>
                    <h3 style={{ fontSize: '1.0625rem' }}>{venue.name}</h3>
                    <p className="caption">
                      {venue.city} · {venue.capacityMax} personas · {venue.depositPercent}% de anticipo
                    </p>
                  </div>
                  <div className="row" style={{ flexWrap: 'wrap' }}>
                    <button
                      type="button"
                      className="btn btn-quiet btn-sm"
                      onClick={() => navigate(`/espacio/${venue.id}`)}
                    >
                      Ver como cliente
                    </button>
                    <button type="button" className="btn btn-quiet btn-sm">
                      Bloquear fecha
                    </button>
                  </div>
                </article>
              </Reveal>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
