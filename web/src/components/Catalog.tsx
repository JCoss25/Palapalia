/**
 * Piezas del catálogo: tarjeta de espacio, tira de fechas, filtros y recibo de apartado.
 *
 * La fecha va primero en toda la interfaz: cada tarjeta responde «¿está libre mi día?» antes
 * que el precio, y el CTA del detalle nombra la fecha en vez de decir «Reservar».
 */

import { useEffect, useMemo, useState } from 'react';

import { availabilityFor, type Quote, type Venue } from '../data/demo';
import { isCaptureMode } from '../lib/capture';
import { countdown, fmt, money } from '../lib/format';
import { Stars } from './Layout';

export function VenueCard({
  venue,
  isAvailable,
  onOpen,
  priority = false,
}: {
  venue: Venue;
  isAvailable: boolean | null;
  onOpen: () => void;
  /** La primera tarjeta de la rejilla es el LCP: se carga de inmediato, no en diferido. */
  priority?: boolean;
}) {
  const cover = venue.images[0]!;
  const eager = priority || isCaptureMode();
  return (
    <button type="button" className="venue-card" onClick={onOpen}>
      <span className="media media-16x10">
        <img
          src={cover.src}
          alt={cover.alt}
          loading={eager ? 'eager' : 'lazy'}
          fetchPriority={priority ? 'high' : 'auto'}
          decoding="async"
        />
        {venue.sponsored ? (
          <span className="badge badge-sponsored">Patrocinado</span>
        ) : venue.instantBook ? (
          <span className="badge">Reserva inmediata</span>
        ) : null}
      </span>
      <span className="row-between" style={{ alignItems: 'flex-start' }}>
        <span>
          <h3>{venue.name}</h3>
          <span className="meta" style={{ display: 'block', marginTop: 2 }}>
            {venue.city}, {venue.state} · {venue.category} · hasta {venue.capacityMax} personas
          </span>
          {isAvailable === true ? (
            <span className="available" style={{ display: 'block', marginTop: 6 }}>
              Libre en tu fecha
            </span>
          ) : isAvailable === false ? (
            <span className="unavailable" style={{ display: 'block', marginTop: 6 }}>
              Ocupado en tu fecha
            </span>
          ) : null}
        </span>
        <Stars rating={venue.rating} reviews={venue.reviews} />
      </span>
      <span className="row-between" style={{ alignItems: 'baseline' }}>
        <span className="meta">Desde</span>
        <span style={{ fontWeight: 600 }}>
          {money(venue.basePriceCents)}
          <span className="meta" style={{ fontWeight: 400 }}> por día</span>
        </span>
      </span>
    </button>
  );
}

export function VenueRow({ venue, onOpen }: { venue: Venue; onOpen: () => void }) {
  const cover = venue.images[0]!;
  return (
    <button
      type="button"
      className="venue-card"
      onClick={onOpen}
      style={{ gridTemplateColumns: '72px 1fr auto', display: 'grid', alignItems: 'center', gap: 'var(--s-3)' }}
    >
      <span className="media media-1x1">
        <img
          src={cover.src}
          alt={cover.alt}
          loading={isCaptureMode() ? 'eager' : 'lazy'}
          decoding="async"
        />
      </span>
      <span>
        <h3 style={{ fontSize: '0.9375rem' }}>{venue.name}</h3>
        <span className="meta" style={{ display: 'block' }}>
          {venue.city} · hasta {venue.capacityMax} personas
        </span>
      </span>
      <span style={{ textAlign: 'right' }}>
        <span style={{ fontWeight: 600, display: 'block' }}>{money(venue.basePriceCents)}</span>
        <span className="meta">por día</span>
      </span>
    </button>
  );
}

export function DateStrip({
  selected,
  onSelect,
  days = 10,
}: {
  selected: Date;
  onSelect: (date: Date) => void;
  days?: number;
}) {
  const options = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return Array.from({ length: days }, (_, index) => {
      const date = new Date(today);
      date.setDate(date.getDate() + index + 1);
      return date;
    });
  }, [days]);

  return (
    <div className="date-strip" role="group" aria-label="Elige la fecha de tu evento">
      {options.map((date) => {
        const isSelected = fmt.isoDate(date) === fmt.isoDate(selected);
        return (
          <button
            key={date.toISOString()}
            type="button"
            className="date-cell"
            aria-pressed={isSelected}
            aria-label={fmt.dayLong(date)}
            onClick={() => onSelect(date)}
          >
            <span>{fmt.weekday(date)}</span>
            <span>{date.getDate()}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Chip({
  label,
  selected,
  onClick,
}: {
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" className="chip" aria-pressed={selected} onClick={onClick}>
      {label}
    </button>
  );
}

/**
 * Recibo de apartado: folio, fecha, desglose y reloj real.
 *
 * El reloj se calcula contra `expiresAt` (no con un contador propio) y se detiene en 00:00.
 */
export function HoldTicket({
  folio,
  venueName,
  venueImage,
  venueImageAlt,
  startAt,
  endAt,
  expiresAt,
  quote,
  currency = 'MXN',
  timezone = 'America/Merida',
  children,
}: {
  folio: string;
  venueName: string;
  venueImage: string;
  venueImageAlt: string;
  startAt: Date;
  endAt: Date;
  expiresAt: Date;
  quote: Quote;
  currency?: string;
  timezone?: string;
  children?: React.ReactNode;
}) {
  const [remaining, setRemaining] = useState(() =>
    Math.max(Math.floor((expiresAt.getTime() - Date.now()) / 1000), 0),
  );

  useEffect(() => {
    setRemaining(Math.max(Math.floor((expiresAt.getTime() - Date.now()) / 1000), 0));
    const timer = window.setInterval(() => {
      setRemaining(Math.max(Math.floor((expiresAt.getTime() - Date.now()) / 1000), 0));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  const expired = remaining <= 0;

  return (
    <div className="ticket">
      <div className="ticket-head">
        <div className="ticket-venue">
          <span className="media">
            <img src={venueImage} alt={venueImageAlt} />
          </span>
          <span>
            <p style={{ fontWeight: 600 }}>{venueName}</p>
            <p className="caption">Folio {folio} · precio congelado al apartar</p>
          </span>
        </div>
        <div className="row-between" style={{ alignItems: 'flex-end' }}>
          <div>
            <p className="ticket-date">{fmt.dayLong(startAt)}</p>
            <p className="caption">
              {fmt.time(startAt)} a {fmt.time(endAt)} · hora de {timezone.replace('_', ' ')}
            </p>
          </div>
          <div style={{ textAlign: 'right' }}>
            <p className="countdown num" data-expired={expired} aria-live="off">
              {countdown(remaining)}
            </p>
            <p className="caption">{expired ? 'apartado vencido' : 'para pagar'}</p>
          </div>
        </div>
        <p className="visually-hidden" aria-live="polite">
          {expired
            ? 'El apartado venció.'
            : `Quedan ${Math.floor(remaining / 60)} minutos y ${remaining % 60} segundos para pagar.`}
        </p>
      </div>

      <div className="perforation" role="presentation" />

      <div className="ticket-body">
        {quote.lines.map((line) => (
          <div key={`${line.concept}-${line.amountCents}`} className="line">
            <span className={`line-label ${line.isDiscount ? 'line-discount' : ''}`.trim()}>
              {line.concept}
              {line.detail ? ` · ${line.detail}` : ''}
            </span>
            <span className={`num ${line.isDiscount ? 'line-discount' : ''}`.trim()}>
              {line.isDiscount ? '−' : ''}
              {money(line.amountCents, currency)}
            </span>
          </div>
        ))}
        <div className="line line-total">
          <span>Total hoy</span>
          <span className="amount num">{money(quote.depositCents, currency)}</span>
        </div>
        <p className="caption">
          Total del evento {money(quote.totalCents, currency)} · saldo {money(quote.totalCents - quote.depositCents, currency)} antes del
          evento
        </p>
      </div>

      {children ? <div style={{ padding: '0 var(--s-4) var(--s-4)' }}>{children}</div> : null}
    </div>
  );
}

export function PolicyNote({ text }: { text: string }) {
  return (
    <div className="policy">
      <p>{text}</p>
      <p style={{ marginTop: 'var(--s-2)' }}>
        <a
          href="#/politica"
          style={{ color: 'var(--sea)', fontWeight: 600 }}
          onClick={(event) => {
            event.preventDefault();
            window.location.hash = '#/politica';
          }}
        >
          Leer la política completa
        </a>
      </p>
    </div>
  );
}

/** Calendario compacto de disponibilidad del venue. */
export function AvailabilityGrid({
  venue,
  selectedDate,
  onSelect,
  days = 14,
}: {
  venue: Venue;
  selectedDate: Date;
  onSelect: (date: Date) => void;
  days?: number;
}) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const availability = availabilityFor(venue, today, days);

  return (
    <div className="date-strip" role="group" aria-label={`Disponibilidad de ${venue.name}`}>
      {availability.map((day) => {
        const isSelected = fmt.isoDate(day.date) === fmt.isoDate(selectedDate);
        const label =
          day.reason === 'blackout'
            ? 'cerrado por el anfitrión'
            : day.reason === 'booked'
              ? 'ya reservado'
              : day.reason === 'min_notice'
                ? 'necesita más anticipación'
                : 'disponible';
        return (
          <button
            key={day.date.toISOString()}
            type="button"
            className="date-cell"
            aria-pressed={isSelected}
            aria-label={`${fmt.dayLong(day.date)}: ${label}`}
            onClick={() => onSelect(day.date)}
            style={!day.isAvailable ? { background: 'var(--bone)', color: 'var(--ink-soft)' } : undefined}
          >
            <span>{fmt.weekday(day.date)}</span>
            <span>{day.date.getDate()}</span>
          </button>
        );
      })}
    </div>
  );
}
