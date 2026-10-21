/**
 * Apartado (checkout).
 *
 * El recibo es el objeto central: folio, fecha, desglose y reloj real. Cuando el reloj llega a
 * cero la pantalla cambia de estado y ofrece volver a apartar, en vez de dejar al usuario atorado.
 */

import { useEffect, useMemo, useState } from 'react';

import { HoldTicket, PolicyNote } from '../components/Catalog';
import { COUPONS, buildQuote, type Quote } from '../data/demo';
import { confirmBooking, createHold, getQuote, getVenue } from '../data/source';
import { fmt, money } from '../lib/format';
import { navigate } from '../lib/router';
import { useAsync } from '../lib/useAsync';

type Method = 'card' | 'spei' | 'cash';

const HOLD_MINUTES = 15;

export function CheckoutPage({
  venueId,
  startIso,
  endIso,
  guests,
}: {
  venueId: string;
  startIso?: string;
  endIso?: string;
  guests: number;
}) {
  const venueQuery = useAsync(() => getVenue(venueId), [venueId]);
  const venue = venueQuery.data;

  const [method, setMethod] = useState<Method>('card');
  const [couponInput, setCouponInput] = useState('');
  const [coupon, setCoupon] = useState<{ code: string; discountCents: number } | null>(null);
  const [couponMessage, setCouponMessage] = useState<string | null>(null);
  const [wantsInvoice, setWantsInvoice] = useState(false);
  const [rfc, setRfc] = useState('');
  const [submitted, setSubmitted] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [paying, setPaying] = useState(false);

  const slot = useMemo(() => {
    const start = startIso ? new Date(startIso) : new Date();
    if (!startIso) start.setHours(15, 0, 0, 0);
    const end = endIso ? new Date(endIso) : new Date(start.getTime() + 11 * 3600_000);
    return { start, end };
  }, [endIso, startIso]);

  const quoteQuery = useAsync(
    () =>
      venue
        ? getQuote(venue, slot.start, slot.end, guests, coupon?.code)
        : Promise.resolve({ data: null as Quote | null, fromFallback: true }),
    [venue?.id, slot.start.toISOString(), guests, coupon?.code],
  );

  const holdQuery = useAsync(
    () =>
      venue
        ? createHold({
            venueId: venue.id,
            startAt: slot.start,
            endAt: slot.end,
            guests,
            quoteTotalCents: undefined,
          })
        : Promise.resolve({ data: null, fromFallback: true }),
    [venue?.id, slot.start.toISOString(), guests],
  );

  const hold = holdQuery.data;
  const expiresAt = useMemo(
    () => (hold ? new Date(hold.expiresAt) : new Date(Date.now() + HOLD_MINUTES * 60_000)),
    [hold],
  );

  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const timer = globalThis.setInterval(() => {
      setExpired(Date.now() >= expiresAt.getTime());
    }, 1000);
    return () => globalThis.clearInterval(timer);
  }, [expiresAt]);

  const quote =
    quoteQuery.data ??
    (venue ? buildQuote({ venue, startAt: slot.start, endAt: slot.end, guests, coupon }) : null);

  if (venueQuery.loading) {
    return (
      <main id="contenido" className="wrap section">
        <p className="caption">Preparando tu apartado…</p>
      </main>
    );
  }

  if (!venue) {
    return (
      <main id="contenido" className="wrap section">
        <h1 className="display display-lg">No encontramos ese apartado</h1>
        <p className="body-soft measure" style={{ marginTop: 'var(--s-3)' }}>
          Los apartados viven 15 minutos. Si cerraste la pestaña, el horario ya se liberó: vuelve a apartarlo
          desde el espacio.
        </p>
        <button type="button" className="btn" style={{ marginTop: 'var(--s-5)' }} onClick={() => navigate('/')}>
          Volver al buscador
        </button>
      </main>
    );
  }

  const applyCoupon = () => {
    const code = couponInput.trim().toUpperCase();
    if (!code) return;
    const found = COUPONS[code];
    if (!found) {
      setCoupon(null);
      setCouponMessage('Ese cupón no existe o ya venció. Revisa el código.');
      return;
    }
    const subtotal = venue.basePriceCents + venue.cleaningCents;
    const discountCents =
      found.kind === 'PERCENT' ? Math.round((subtotal * found.value) / 100) : found.value;
    setCoupon({ code, discountCents });
    setCouponMessage(`${found.note} aplicado a esta reserva.`);
  };

  const handlePay = async () => {
    setError(null);
    if (expired) {
      setError('El apartado venció y el horario se liberó. Vuelve a apartarlo: toma unos segundos.');
      return;
    }
    if (wantsInvoice && rfc.trim().length < 12) {
      setError('Para facturar necesitamos tu RFC completo (12 o 13 caracteres).');
      return;
    }
    setPaying(true);
    try {
      const result = await confirmBooking({
        holdId: hold?.id ?? 'demo',
        method,
        rfc: wantsInvoice ? rfc : undefined,
      });
      setSubmitted(result.data.folio);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'No pudimos completar el pago. Intenta con otro método.',
      );
    } finally {
      setPaying(false);
    }
  };

  if (submitted) {
    return (
      <main id="contenido" className="wrap section">
        <div className="stack-lg" style={{ maxWidth: '58ch' }}>
          <h1 className="display display-lg">Reserva confirmada.</h1>
          <p className="lead">
            {venue.name} te espera el {fmt.dayLong(slot.start)} a las {fmt.time(slot.start)}. El folio de tu
            reserva es <strong>{submitted}</strong>.
          </p>
          <div className="notice notice-info">
            <strong>
              {method === 'cash'
                ? 'Acordaste pagar directo al anfitrión'
                : `Pagaste el anticipo de ${quote ? money(quote.depositCents) : '—'}`}
            </strong>
            <p>
              {method === 'cash'
                ? 'Sin comisión de servicio, pero sin protección de pago de Quinta. El contrato sigue vigente.'
                : `El saldo de ${quote ? money(quote.totalCents - quote.depositCents) : '—'} se cobra antes del evento y te avisamos por correo.`}
            </p>
          </div>
          <div className="notice">
            <strong>Lo que sigue</strong>
            <p>
              Te enviamos el contrato con el desglose congelado, y recordatorios 24 horas y 2 horas antes del
              evento. El depósito de garantía de {money(venue.securityDepositCents)} se libera 48 horas después
              si no hay daños documentados.
            </p>
          </div>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={() => navigate('/reservas')}>
              Ver mis reservas
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => navigate('/')}>
              Buscar otro espacio
            </button>
          </div>
        </div>
      </main>
    );
  }

  // A partir de aquí el espacio existe, así que la cotización siempre está disponible
  // (la de la API si respondió, la del motor local si no).
  const activeQuote: Quote =
    quote ?? buildQuote({ venue, startAt: slot.start, endAt: slot.end, guests, coupon });
  const folio = hold?.folio ?? 'HD-PENDIENTE';

  return (
    <main id="contenido" className="section-tight">
      <div className="wrap">
        <div className="row-between" style={{ marginBottom: 'var(--s-5)' }}>
          <button type="button" className="btn btn-quiet btn-sm" onClick={() => navigate(`/espacio/${venue.id}`)}>
            Volver al espacio
          </button>
          <p className="caption">Apartado {folio}</p>
        </div>

        <div className="split">
          <div className="stack-lg">
            <h1 className="display display-lg">Aparta tu fecha.</h1>

            <HoldTicket
              folio={folio}
              venueName={venue.name}
              venueImage={venue.images[0]!.src}
              venueImageAlt={venue.images[0]!.alt}
              startAt={slot.start}
              endAt={slot.end}
              expiresAt={expiresAt}
              quote={activeQuote}
            />

            {expired ? (
              <div className="notice notice-warning" role="alert">
                <strong>Tu apartado venció</strong>
                <p>
                  El horario se liberó y alguien más puede tomarlo. Si sigues interesado, aparta de nuevo: el
                  precio se recalcula al momento.
                </p>
                <button
                  type="button"
                  className="btn btn-quiet btn-sm"
                  onClick={() => navigate(`/espacio/${venue.id}`)}
                >
                  Volver a apartar
                </button>
              </div>
            ) : null}

            {activeQuote.warnings.length > 0 ? (
              <div className="notice">
                <strong>Antes de pagar</strong>
                <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0, gap: 'var(--s-2)' }}>
                  {activeQuote.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <section className="stack" style={{ gap: 'var(--s-3)' }}>
              <h2 className="display display-md">Cómo quieres pagar</h2>
              <MethodRow
                label="Tarjeta"
                detail="Cobro con Stripe. Puedes guardar la tarjeta para el saldo."
                selected={method === 'card'}
                onSelect={() => setMethod('card')}
              />
              <MethodRow
                label="Transferencia SPEI"
                detail="Recibes la CLABE al confirmar. El horario queda apartado hasta que se acredita."
                selected={method === 'spei'}
                onSelect={() => setMethod('spei')}
              />
              <MethodRow
                label="Pago directo al anfitrión"
                detail="Sin comisión de servicio, sin protección de pago de Quinta."
                selected={method === 'cash'}
                onSelect={() => setMethod('cash')}
              />
            </section>

            <section className="stack" style={{ gap: 'var(--s-3)' }}>
              <h2 className="display display-md">Cupón</h2>
              <div className="row" style={{ flexWrap: 'wrap' }}>
                <div className="field grow" style={{ minWidth: 220 }}>
                  <label htmlFor="cupon">Código</label>
                  <input
                    id="cupon"
                    value={couponInput}
                    onChange={(event) => setCouponInput(event.target.value.toUpperCase())}
                    placeholder="FIESTA10"
                  />
                </div>
                <button type="button" className="btn btn-quiet" onClick={applyCoupon} style={{ alignSelf: 'end' }}>
                  Aplicar
                </button>
              </div>
              {couponMessage ? (
                <p className="caption" role="status">
                  {couponMessage}
                </p>
              ) : null}
              <p className="caption">
                Cupones de ejemplo para probar: FIESTA10 (10%), XCAMBOCASH ($500), TEMPORADA (15%).
              </p>
            </section>

            <section className="stack" style={{ gap: 'var(--s-3)' }}>
              <h2 className="display display-md">Factura</h2>
              <label className="row" style={{ gap: 'var(--s-2)' }}>
                <input
                  type="checkbox"
                  checked={wantsInvoice}
                  onChange={(event) => setWantsInvoice(event.target.checked)}
                />
                <span>Necesito factura (CFDI 4.0)</span>
              </label>
              {wantsInvoice ? (
                <div className="field" style={{ maxWidth: 360 }}>
                  <label htmlFor="rfc">RFC</label>
                  <input
                    id="rfc"
                    value={rfc}
                    onChange={(event) => setRfc(event.target.value.toUpperCase())}
                    placeholder="XAXX010101000"
                    maxLength={13}
                  />
                  <p className="caption">Se solicita al PAC al confirmar el pago. Puedes corregirlo después.</p>
                </div>
              ) : null}
            </section>

            {error ? (
              <div className="notice notice-danger" role="alert">
                <strong>No pudimos completar el pago</strong>
                <p>{error}</p>
              </div>
            ) : null}
          </div>

          <aside className="sticky-aside stack" style={{ gap: 'var(--s-4)' }}>
            <div className="surface stack" style={{ padding: 'var(--s-5)', gap: 'var(--s-4)' }}>
              <div className="stack" style={{ gap: 'var(--s-2)' }}>
                <div className="line">
                  <span className="line-label">Renta y limpieza</span>
                  <span className="num">{money(activeQuote.subtotalCents)}</span>
                </div>
                {activeQuote.discountCents > 0 ? (
                  <div className="line">
                    <span className="line-label line-discount">Descuento</span>
                    <span className="num line-discount">-{money(activeQuote.discountCents)}</span>
                  </div>
                ) : null}
                <div className="line">
                  <span className="line-label">Comisión e IVA</span>
                  <span className="num">{money(activeQuote.serviceFeeCents + activeQuote.taxCents)}</span>
                </div>
                <div className="line">
                  <span className="line-label">Depósito de garantía</span>
                  <span className="num">{money(activeQuote.securityDepositCents)}</span>
                </div>
                <div className="line line-total">
                  <span>Total hoy</span>
                  <span className="amount num">{money(activeQuote.depositCents)}</span>
                </div>
              </div>

              <button
                type="button"
                className="btn btn-block"
                onClick={() => void handlePay()}
                disabled={expired || paying}
              >
                {expired
                  ? 'El apartado venció'
                  : paying
                    ? 'Procesando el pago…'
                    : `Pagar ${money(activeQuote.depositCents)} y apartar`}
              </button>
              <p className="caption">
                Al pagar aceptas el contrato de apartado y la política de cancelación. La aceptación
                electrónica equivale a firma.
              </p>
            </div>

            <PolicyNote
              text={`Cancelación escalonada: 100% hasta 30 días antes del evento, 50% entre 7 y 30 días, sin reembolso del anticipo a menos de 7 días. Si el anfitrión cancela, el reembolso es total. Depósito de garantía de ${money(venue.securityDepositCents)}, reembolsable.`}
            />
          </aside>
        </div>
      </div>
    </main>
  );
}

function MethodRow({
  label,
  detail,
  selected,
  onSelect,
}: {
  label: string;
  detail: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      className="radio-row"
      onClick={onSelect}
    >
      <span className="grow">
        <span style={{ fontWeight: 500, display: 'block' }}>{label}</span>
        <span className="caption">{detail}</span>
      </span>
      <span className="radio-dot" aria-hidden="true" />
    </button>
  );
}
