/**
 * Mis reservas.
 *
 * La próxima reserva se lee como un pase (folio, días restantes, siguiente acción) y el resto es
 * historial compacto. Sin tarjetas idénticas en fila.
 */

import { useMemo, useState } from 'react';

import { Reveal, SectionHead } from '../components/Layout';
import { VENUES_BY_ID, refundTier } from '../data/demo';
import { listMyBookings } from '../data/source';
import { daysUntil, fmt, money } from '../lib/format';
import { navigate } from '../lib/router';
import { useAsync } from '../lib/useAsync';

type Tab = 'proximas' | 'pasadas' | 'canceladas';

const STATUS_LABEL: Record<string, string> = {
  CONFIRMED: 'Confirmada',
  PENDING_PAYMENT: 'Falta pagar',
  COMPLETED: 'Completada',
  CANCELLED_BY_CLIENT: 'Cancelada por ti',
  CANCELLED_BY_PROVIDER: 'Cancelada por el anfitrión',
  REFUNDED: 'Reembolsada',
};

export function BookingsPage() {
  const bookingsQuery = useAsync(() => listMyBookings(), []);
  const bookings = useMemo(() => bookingsQuery.data ?? [], [bookingsQuery.data]);
  const [tab, setTab] = useState<Tab>('proximas');
  const [message, setMessage] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const now = Date.now();
    return {
      proximas: bookings.filter(
        (booking) => new Date(booking.startAt).getTime() >= now && booking.state === 'CONFIRMED',
      ),
      pasadas: bookings.filter((booking) => booking.state === 'COMPLETED'),
      canceladas: bookings.filter((booking) => booking.state.startsWith('CANCELLED') || booking.state === 'REFUNDED'),
    };
  }, [bookings]);

  const list = grouped[tab];
  const [next, ...history] = list;

  const handleCancel = (bookingId: string) => {
    const booking = bookings.find((item) => item.id === bookingId);
    if (!booking) return;
    const venue = VENUES_BY_ID.get(booking.venueId);
    if (!venue) return;
    const days = daysUntil(booking.startAt);
    const tier = refundTier(days);
    const refundable = Math.max(booking.paidCents - venue.securityDepositCents, 0);
    const refund = Math.round((refundable * tier.percent) / 100);
    setMessage(
      `Reserva ${booking.folio} cancelada. ${tier.explanation} Reembolso de ${money(refund)} más el depósito de garantía de ${money(venue.securityDepositCents)}.`,
    );
  };

  return (
    <main id="contenido" className="section-tight">
      <div className="wrap">
        <div className="stack" style={{ gap: 'var(--s-2)', marginBottom: 'var(--s-5)' }}>
          <h1 className="display display-lg">Mis reservas</h1>
          <p className="body-soft">Todo lo que ya apartaste, con el estado del pago y lo que sigue.</p>
        </div>

        <div className="tabs" role="tablist" aria-label="Filtrar reservas" style={{ maxWidth: 460 }}>
          {(
            [
              ['proximas', 'Próximas'],
              ['pasadas', 'Pasadas'],
              ['canceladas', 'Canceladas'],
            ] as Array<[Tab, string]>
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              onClick={() => {
                setTab(value);
                setMessage(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {message ? (
          <div className="notice notice-info" role="status" style={{ marginTop: 'var(--s-5)' }}>
            <strong>Listo</strong>
            <p>{message}</p>
          </div>
        ) : null}

        {!next ? (
          <div className="surface" style={{ padding: 'var(--s-6)', marginTop: 'var(--s-5)' }}>
            <h2 className="display display-md">
              {tab === 'proximas' ? 'No tienes eventos próximos' : 'Nada por aquí todavía'}
            </h2>
            <p className="body-soft measure" style={{ marginTop: 'var(--s-3)' }}>
              Cuando apartes una fecha aparecerá aquí con su folio, el saldo pendiente y la siguiente acción.
            </p>
            <button type="button" className="btn" style={{ marginTop: 'var(--s-5)' }} onClick={() => navigate('/')}>
              Buscar un espacio
            </button>
          </div>
        ) : (
          <div className="stack-lg" style={{ marginTop: 'var(--s-5)' }}>
            <Reveal>
              <article className="pass">
                <header className="pass-strip">
                  <span>
                    {daysUntil(next.startAt) > 1
                      ? `Faltan ${daysUntil(next.startAt)} días`
                      : daysUntil(next.startAt) === 1
                        ? 'Es mañana'
                        : daysUntil(next.startAt) === 0
                          ? 'Es hoy'
                          : 'En curso'}
                  </span>
                  <span>{next.folio}</span>
                </header>
                <div className="pass-body">
                  <div className="row" style={{ alignItems: 'flex-start', gap: 'var(--s-4)' }}>
                    {(() => {
                      const venue = VENUES_BY_ID.get(next.venueId);
                      if (!venue) return null;
                      const cover = venue.images[0]!;
                      return (
                        <>
                          <span className="media media-1x1" style={{ width: 72, flex: '0 0 72px' }}>
                            <img src={cover.src} alt={cover.alt} />
                          </span>
                          <span className="grow">
                            <h2 style={{ fontSize: '1.125rem' }}>{venue.name}</h2>
                            <p className="caption">
                              {fmt.dayLong(new Date(next.startAt))} · {fmt.time(new Date(next.startAt))} a{' '}
                              {fmt.time(new Date(next.endAt))}
                            </p>
                            <p className="caption">
                              {next.guests} invitados ·{' '}
                              {next.paidCents < next.totalCents
                                ? `saldo ${money(next.totalCents - next.paidCents)}`
                                : 'pagado por completo'}
                            </p>
                          </span>
                          <span className="status status-ok">{STATUS_LABEL[next.state]}</span>
                        </>
                      );
                    })()}
                  </div>

                  <div className="row" style={{ flexWrap: 'wrap' }}>
                    <button type="button" className="btn btn-sm">
                      Pagar saldo
                    </button>
                    <button
                      type="button"
                      className="btn btn-quiet btn-sm"
                      onClick={() =>
                        setMessage(
                          'El contrato se genera con el desglose congelado al apartar. En producción se descarga en PDF firmado con folio.',
                        )
                      }
                    >
                      Ver contrato
                    </button>
                    <button
                      type="button"
                      className="btn btn-quiet btn-sm"
                      onClick={() => handleCancel(next.id)}
                    >
                      Cancelar reserva
                    </button>
                  </div>

                  <p className="caption">
                    Si necesitas cambiar la fecha, reprogramar conserva el precio original del apartado. Habla
                    con el anfitrión desde la reserva.
                  </p>
                </div>
              </article>
            </Reveal>

            {history.length > 0 ? (
              <div>
                <SectionHead title="Historial" note="Puedes reseñar los eventos que ya pasaron." />
                <div className="hairline-list">
                  {history.map((booking, index) => {
                    const venue = VENUES_BY_ID.get(booking.venueId);
                    if (!venue) return null;
                    const cover = venue.images[0]!;
                    return (
                      <Reveal key={booking.id} delay={index * 60}>
                        <div className="history-row">
                          <span className="media media-1x1">
                            <img src={cover.src} alt={cover.alt} loading="lazy" />
                          </span>
                          <span className="grow">
                            <p style={{ fontWeight: 600 }}>{venue.name}</p>
                            <p className="caption">
                              {fmt.dayLong(new Date(booking.startAt))} · {booking.guests} invitados ·{' '}
                              {money(booking.totalCents)}
                            </p>
                          </span>
                          {booking.canReview ? (
                            <button
                              type="button"
                              className="btn btn-quiet btn-sm"
                              onClick={() =>
                                setMessage(
                                  `Las reseñas sólo se pueden escribir sobre eventos completados y dentro de 30 días. Se publican verificadas con la reserva ${booking.folio}.`,
                                )
                              }
                            >
                              Escribir reseña
                            </button>
                          ) : (
                            <span
                              className={`status ${booking.state.startsWith('CANCELLED') ? 'status-danger' : ''}`.trim()}
                            >
                              {STATUS_LABEL[booking.state]}
                            </span>
                          )}
                        </div>
                      </Reveal>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </main>
  );
}
