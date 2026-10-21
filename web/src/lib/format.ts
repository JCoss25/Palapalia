/**
 * Formato de dinero, fechas y tiempo.
 *
 * Dinero: siempre en centavos enteros; el formateo es lo único que ve decimales.
 */

export function money(cents: number, currency = 'MXN'): string {
  return new Intl.NumberFormat('es-MX', {
    style: 'currency',
    currency,
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);
}

export function centsFromInput(value: string): number {
  const parsed = Number(value.replace(/[^\d.]/g, ''));
  return Number.isFinite(parsed) ? Math.round(parsed * 100) : 0;
}

const DAY = new Intl.DateTimeFormat('es-MX', { weekday: 'short' });
const DAY_LONG = new Intl.DateTimeFormat('es-MX', { weekday: 'long', day: 'numeric', month: 'long' });
const DATE_SHORT = new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short' });
const MONTH_YEAR = new Intl.DateTimeFormat('es-MX', { month: 'long', year: 'numeric' });
const TIME = new Intl.DateTimeFormat('es-MX', { hour: '2-digit', minute: '2-digit', hour12: false });

export const fmt = {
  weekday: (date: Date) => DAY.format(date).replace('.', ''),
  dayLong: (date: Date) => DAY_LONG.format(date),
  dateShort: (date: Date) => DATE_SHORT.format(date),
  monthYear: (date: Date) => MONTH_YEAR.format(date),
  time: (date: Date) => TIME.format(date),
  /** Fecha local en formato ISO corto, para query params. */
  isoDate: (date: Date) => {
    const offset = date.getTimezoneOffset() * 60000;
    return new Date(date.getTime() - offset).toISOString().slice(0, 10);
  },
};

export function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function startOfDay(date: Date): Date {
  const next = new Date(date);
  next.setHours(0, 0, 0, 0);
  return next;
}

/** Días completos entre hoy y una fecha (para la cuenta regresiva del pase). */
export function daysUntil(iso: string): number {
  const target = startOfDay(new Date(iso));
  const today = startOfDay(new Date());
  return Math.round((target.getTime() - today.getTime()) / 86400000);
}

export function countdown(seconds: number): string {
  const clamped = Math.max(Math.floor(seconds), 0);
  const mm = String(Math.floor(clamped / 60)).padStart(2, '0');
  const ss = String(clamped % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** "hasta 120 personas" · "120 personas" */
export function guestsLabel(count: number): string {
  return `${count} ${count === 1 ? 'persona' : 'personas'}`;
}
