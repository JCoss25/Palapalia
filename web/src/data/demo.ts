/**
 * Datos de demostración y motor de cotización del cliente.
 *
 * El motor replica las reglas del backend (`app/services/pricing.py`) para que las cifras de la
 * demo sean las mismas que devolvería la API: comisión 7.5% sobre el subtotal con descuento,
 * IVA 16% sobre la comisión, anticipo 30%, limpieza y depósito de garantía fuera del total.
 * Cuando la API está disponible, estos datos se ignoran (ver `api/client.ts`).
 */

export type SlotKind = 'FULL_DAY' | 'MORNING' | 'EVENING' | 'NIGHT';

export interface Venue {
  id: string;
  name: string;
  category: 'Palapa' | 'Quinta' | 'Salón' | 'Terraza';
  city: string;
  state: string;
  headline: string;
  description: string;
  capacityMin: number;
  capacityMax: number;
  minHours: number;
  bufferMinutes: number;
  basePriceCents: number;
  extraGuestFrom: number;
  extraGuestCents: number;
  cleaningCents: number;
  securityDepositCents: number;
  depositPercent: number;
  minNoticeHours: number;
  instantBook: boolean;
  sponsored: boolean;
  rating: number;
  reviews: number;
  eventTypes: string[];
  includes: string[];
  amenities: string[];
  houseRules: string;
  images: { src: string; alt: string }[];
  /** Ocupación simulada: días del mes marcados como no disponibles. */
  bookedOffsets: number[];
  /**
   * Disponibilidad que devuelve la API para la fecha consultada (`GET /venues` con `date_from`).
   * Cuando viene del respaldo local se calcula con `availabilityFor`.
   */
  availableOnDate?: boolean | null;
}

/**
 * Prefijo de assets.
 *
 * Con `base: '/'` (servidor) devuelve `/images/...`; con `base: './'` (build de un solo archivo para
 * abrir con doble clic) devuelve `./images/...`, que es lo único que funciona desde `file://`.
 */
const asset = (path: string): string => `${import.meta.env.BASE_URL}${path.replace(/^\//, '')}`;

const IMG = {
  palapaDay: { src: asset('/images/palapa-dia.jpg'), alt: 'Palapa de palma frente al mar con sombrillas de playa' },
  palapaNight: {
    src: asset('/images/palapa-noche.jpg'),
    alt: 'Palapas de palma iluminadas junto al mar al anochecer',
  },
  garden: {
    src: asset('/images/quinta-jardin.jpg'),
    alt: 'Jardín de una quinta con macetones de piedra y camino de grava',
  },
  bar: { src: asset('/images/salon-barra.jpg'), alt: 'Barra iluminada bajo luces de cadena en un espacio abierto' },
  pool: { src: asset('/images/alberca.jpg'), alt: 'Alberca frente al mar Caribe con una palapa al fondo' },
  palms: { src: asset('/images/palma-textura.jpg'), alt: 'Tejido de hojas de palma seca a contraluz' },
} as const;

export const CATEGORIES = ['Todas', 'Palapa', 'Quinta', 'Salón', 'Terraza'] as const;
export const EVENT_TYPES = ['XV años', 'Boda', 'Cumpleaños', 'Corporativo', 'Baby shower'] as const;

export const VENUES: Venue[] = [
  {
    id: 'palapa-xcambo',
    name: 'Palapa Xcambó',
    category: 'Palapa',
    city: 'Telchac Puerto',
    state: 'Yucatán',
    headline: 'Palapa de 12×8 m frente al mar, con cocina fría y luz cálida',
    description:
      'Una palapa abierta al mar, a dos minutos de la playa de Xcambó. Piso de concreto pulido, techo de palma de 12×8 m y luz cálida colgada. Funciona igual para una comida de día que para una fiesta que termina a las dos de la mañana.',
    capacityMin: 20,
    capacityMax: 120,
    minHours: 4,
    bufferMinutes: 60,
    basePriceCents: 840_000,
    extraGuestFrom: 20,
    extraGuestCents: 3_000,
    cleaningCents: 65_000,
    securityDepositCents: 200_000,
    depositPercent: 30,
    minNoticeHours: 24,
    instantBook: true,
    sponsored: true,
    rating: 4.9,
    reviews: 38,
    eventTypes: ['XV años', 'Cumpleaños', 'Corporativo'],
    includes: [
      'Palapa de 12×8 m con piso de concreto pulido y luz cálida',
      '50 sillas, 6 mesas redondas y área de cocina fría',
      'Limpieza final y montaje desde las 14:00',
      'Vigilancia y acceso a la playa para tus invitados',
    ],
    amenities: ['Asador', 'Cocina fría', 'Estacionamiento 40 autos', 'Baños', 'Regaderas', 'Generador eléctrico'],
    houseRules:
      'Sin pirotecnia. Música en vivo hasta las 01:00. Aforo máximo 120 personas. Se permite proveedor externo de alimentos con aviso previo.',
    images: [IMG.palapaNight, IMG.palapaDay, IMG.palms],
    bookedOffsets: [3, 4, 11, 12, 18, 25, 26],
  },
  {
    id: 'quinta-alcantara',
    name: 'Quinta Alcántara',
    category: 'Quinta',
    city: 'Santiago de Querétaro',
    state: 'Querétaro',
    headline: 'Jardín de 2,000 m² con capilla, alberca y salón techado para 250',
    description:
      'Casa de campo restaurada con jardín, capilla y salón techado. La boda se hace afuera y la cena adentro, sin mover a nadie de lugar. Tiene habitaciones para los novios y estacionamiento propio.',
    capacityMin: 60,
    capacityMax: 250,
    minHours: 8,
    bufferMinutes: 120,
    basePriceCents: 2_200_000,
    extraGuestFrom: 150,
    extraGuestCents: 4_500,
    cleaningCents: 180_000,
    securityDepositCents: 500_000,
    depositPercent: 30,
    minNoticeHours: 72,
    instantBook: false,
    sponsored: false,
    rating: 4.7,
    reviews: 112,
    eventTypes: ['Boda', 'XV años', 'Corporativo'],
    includes: [
      'Jardín de 2,000 m², capilla y salón techado con clima',
      'Suite para los anfitriones la noche del evento',
      'Montaje de sillas y mesas según el plano acordado',
      'Coordinador del espacio durante todo el evento',
    ],
    amenities: ['Alberca', 'Capilla', 'Salón con clima', 'Cocina industrial', 'Estacionamiento 120 autos', 'Planta de luz'],
    houseRules:
      'Hora límite 02:00. Sin fuegos artificiales. Se permite catering externo con registro previo. Evento termina con limpieza incluida.',
    images: [IMG.garden, IMG.pool, IMG.bar],
    bookedOffsets: [5, 6, 13, 19, 20, 27],
  },
  {
    id: 'salon-casa-nube',
    name: 'Salón Casa Nube',
    category: 'Salón',
    city: 'Guadalajara',
    state: 'Jalisco',
    headline: 'Salón techado con barra, pista de baile y sonido incluido',
    description:
      'Salón urbano en la colonia Americana, con barra equipada, pista de baile y sonido profesional incluido. Ideal para cumpleaños y eventos de empresa que no quieren montar nada.',
    capacityMin: 40,
    capacityMax: 150,
    minHours: 5,
    bufferMinutes: 60,
    basePriceCents: 1_450_000,
    extraGuestFrom: 100,
    extraGuestCents: 2_800,
    cleaningCents: 95_000,
    securityDepositCents: 250_000,
    depositPercent: 40,
    minNoticeHours: 48,
    instantBook: true,
    sponsored: false,
    rating: 4.8,
    reviews: 64,
    eventTypes: ['Cumpleaños', 'Corporativo', 'Baby shower'],
    includes: [
      'Salón techado de 320 m² con clima y pista de baile',
      'Barra equipada y sonido profesional con operador',
      'Meseros por 5 horas (hasta 6)',
      'Seguridad privada en la entrada',
    ],
    amenities: ['Barra', 'Sonido', 'Pista de baile', 'Clima', 'Baños amplios', 'Accesibilidad'],
    houseRules:
      'Aforo máximo 150. Sin botella traída de fuera (se permite pastel y postres). Hora límite 02:00 los viernes y sábados.',
    images: [IMG.bar, IMG.palms, IMG.garden],
    bookedOffsets: [2, 9, 16, 17, 24],
  },
  {
    id: 'quinta-los-arcos',
    name: 'Quinta Los Arcos',
    category: 'Quinta',
    city: 'Mérida',
    state: 'Yucatán',
    headline: 'Casona con arcos de piedra, patio central y salón para 180',
    description:
      'Casona del siglo XIX restaurada. Los arcos del patio central son el fondo natural de la ceremonia; el salón techado queda a un paso, sin que nadie cruce la calle.',
    capacityMin: 40,
    capacityMax: 180,
    minHours: 6,
    bufferMinutes: 90,
    basePriceCents: 1_680_000,
    extraGuestFrom: 120,
    extraGuestCents: 3_200,
    cleaningCents: 120_000,
    securityDepositCents: 300_000,
    depositPercent: 30,
    minNoticeHours: 48,
    instantBook: false,
    sponsored: false,
    rating: 4.6,
    reviews: 41,
    eventTypes: ['Boda', 'XV años', 'Corporativo'],
    includes: [
      'Patio central con arcos de piedra originales',
      'Salón techado con clima para 180 personas',
      'Área para ceremonia y área para cena',
      'Baños restaurados y vestidor',
    ],
    amenities: ['Patio', 'Salón con clima', 'Vestidor', 'Estacionamiento 60 autos', 'Cocina'],
    houseRules: 'Hora límite 01:00. Sin pirotecnia. Aforo máximo 180 personas.',
    images: [IMG.garden, IMG.palms, IMG.palapaDay],
    bookedOffsets: [7, 8, 14, 21, 22, 28],
  },
  {
    id: 'palapa-el-faro',
    name: 'Palapa El Faro',
    category: 'Palapa',
    city: 'Puerto Escondido',
    state: 'Oaxaca',
    headline: 'Palapa sobre la arena con vista al faro y cocina equipada',
    description:
      'La palapa está sobre la arena, a 200 metros del faro. Cocina equipada, regaderas y área de hamacas. Perfecta para grupos que quieren quedarse hasta que se vaya la luz.',
    capacityMin: 15,
    capacityMax: 80,
    minHours: 4,
    bufferMinutes: 60,
    basePriceCents: 690_000,
    extraGuestFrom: 15,
    extraGuestCents: 2_500,
    cleaningCents: 55_000,
    securityDepositCents: 150_000,
    depositPercent: 30,
    minNoticeHours: 24,
    instantBook: true,
    sponsored: false,
    rating: 4.8,
    reviews: 29,
    eventTypes: ['Cumpleaños', 'Baby shower', 'Corporativo'],
    includes: [
      'Palapa sobre la arena con vista al faro',
      'Cocina equipada, asador y refrigeración',
      'Regaderas y área de hamacas',
      'Limpieza final incluida',
    ],
    amenities: ['Asador', 'Cocina', 'Regaderas', 'Hamacas', 'Estacionamiento 25 autos'],
    houseRules: 'Sin fogatas en la arena. Música hasta las 00:00. Aforo máximo 80 personas.',
    images: [IMG.palapaDay, IMG.pool, IMG.palapaNight],
    bookedOffsets: [4, 10, 15, 23],
  },
  {
    id: 'terraza-mirador',
    name: 'Terraza Mirador',
    category: 'Terraza',
    city: 'Ciudad de México',
    state: 'CDMX',
    headline: 'Terraza en azotea con vista al centro, barra y DJ booth',
    description:
      'Azotea de un edificio de los años cuarenta, con vista al centro histórico. Barra, DJ booth y área lounge. Ideal para eventos de empresa y lanzamientos.',
    capacityMin: 30,
    capacityMax: 100,
    minHours: 4,
    bufferMinutes: 60,
    basePriceCents: 1_250_000,
    extraGuestFrom: 60,
    extraGuestCents: 3_600,
    cleaningCents: 80_000,
    securityDepositCents: 200_000,
    depositPercent: 40,
    minNoticeHours: 48,
    instantBook: false,
    sponsored: false,
    rating: 4.5,
    reviews: 52,
    eventTypes: ['Corporativo', 'Cumpleaños', 'Baby shower'],
    includes: [
      'Terraza de 280 m² con vista al centro histórico',
      'Barra equipada y DJ booth con sonido',
      'Área lounge con calentadores',
      'Elevador privado para invitados',
    ],
    amenities: ['Barra', 'DJ booth', 'Calentadores', 'Elevador privado', 'Baños'],
    houseRules: 'Hora límite 01:00. Aforo máximo 100. Sin confeti ni pirotecnia.',
    images: [IMG.bar, IMG.pool, IMG.garden],
    bookedOffsets: [1, 6, 12, 18, 26],
  },
];

export const VENUES_BY_ID = new Map(VENUES.map((venue) => [venue.id, venue]));

// ── Cotización ────────────────────────────────────────────────────────────────

export interface QuoteLine {
  concept: string;
  detail?: string;
  amountCents: number;
  isDiscount?: boolean;
  isRefundable?: boolean;
}

export interface Quote {
  venueId: string;
  hours: number;
  guests: number;
  lines: QuoteLine[];
  subtotalCents: number;
  extrasCents: number;
  cleaningCents: number;
  discountCents: number;
  serviceFeeCents: number;
  taxCents: number;
  securityDepositCents: number;
  totalCents: number;
  depositCents: number;
  currency: string;
  warnings: string[];
}

export const PLATFORM_FEE_BPS = 750; // 7.5%
export const TAX_RATE = 0.16;

export function buildQuote(input: {
  venue: Venue;
  startAt: Date;
  endAt: Date;
  guests: number;
  coupon?: { code: string; discountCents: number } | null;
}): Quote {
  const { venue, startAt, endAt, guests } = input;
  const hours = Math.max(Math.round(((endAt.getTime() - startAt.getTime()) / 3600000) * 2) / 2, 0);
  const lines: QuoteLine[] = [];

  let base = venue.basePriceCents;
  const extraGuests = Math.max(guests - venue.extraGuestFrom, 0);
  if (extraGuests > 0) {
    const extra = extraGuests * venue.extraGuestCents;
    base += extra;
    lines.push({
      concept: 'Invitados adicionales',
      detail: `${extraGuests} × ${(venue.extraGuestCents / 100).toLocaleString('es-MX', { style: 'currency', currency: 'MXN', maximumFractionDigits: 0 })}`,
      amountCents: extra,
    });
  }
  lines.unshift({
    concept: 'Renta del espacio',
    detail: `${hours} h · tarifa de día completo`,
    amountCents: base - (extraGuests > 0 ? extraGuests * venue.extraGuestCents : 0),
  });

  const cleaning = venue.cleaningCents;
  lines.push({ concept: 'Limpieza y montaje', amountCents: cleaning });

  const subtotal = base + cleaning;
  const discount = Math.min(input.coupon?.discountCents ?? 0, subtotal);
  if (discount > 0) {
    lines.push({
      concept: `Cupón ${input.coupon?.code ?? ''}`.trim(),
      detail: 'Descuento aplicado',
      amountCents: discount,
      isDiscount: true,
    });
  }

  const taxable = subtotal - discount;
  const serviceFee = Math.round((taxable * PLATFORM_FEE_BPS) / 10000);
  if (serviceFee > 0) {
    lines.push({ concept: 'Comisión de servicio', detail: '7.5%', amountCents: serviceFee });
  }
  const tax = Math.round(serviceFee * TAX_RATE);
  if (tax > 0) {
    lines.push({ concept: 'IVA sobre la comisión', detail: '16%', amountCents: tax });
  }
  if (venue.securityDepositCents > 0) {
    lines.push({
      concept: 'Depósito de garantía',
      detail: 'Reembolsable, se libera 48 h después del evento',
      amountCents: venue.securityDepositCents,
      isRefundable: true,
    });
  }

  const total = taxable + serviceFee + tax + venue.securityDepositCents;
  const deposit = venue.depositPercent === 0 ? total : Math.round((total * venue.depositPercent) / 100);

  const warnings: string[] = [];
  if (venue.bufferMinutes > 0) {
    warnings.push(
      `Se reservan ${venue.bufferMinutes} min de limpieza después de tu evento; ya están bloqueados en el calendario.`,
    );
  }
  if (venue.depositPercent < 100) {
    warnings.push(
      `El saldo restante se cobra ${venue.minNoticeHours >= 72 ? '7' : '5'} días antes del evento, con recordatorio por correo.`,
    );
  }

  return {
    venueId: venue.id,
    hours,
    guests,
    lines,
    subtotalCents: subtotal,
    extrasCents: extraGuests * venue.extraGuestCents,
    cleaningCents: cleaning,
    discountCents: discount,
    serviceFeeCents: serviceFee,
    taxCents: tax,
    securityDepositCents: venue.securityDepositCents,
    totalCents: total,
    depositCents: deposit,
    currency: 'MXN',
    warnings,
  };
}

/** Política escalonada estándar: 100% >30 d, 50% 7–30 d, 0% <7 d. */
export function refundTier(daysUntilEvent: number): { percent: number; explanation: string } {
  if (daysUntilEvent >= 30) {
    return {
      percent: 100,
      explanation: `Reembolso total: cancelas con ${daysUntilEvent.toFixed(0)} días de anticipación.`,
    };
  }
  if (daysUntilEvent >= 7) {
    return {
      percent: 50,
      explanation: 'Reembolso del 50%: cancelas entre 7 y 30 días antes del evento.',
    };
  }
  return {
    percent: 0,
    explanation:
      'Sin reembolso del anticipo: cancelas a menos de 7 días del evento. El depósito de garantía sí se libera.',
  };
}

/** Disponibilidad simulada: reglas de apertura − días ocupados. */
export function availabilityFor(venue: Venue, from: Date, days: number) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Array.from({ length: days }, (_, index) => {
    const date = new Date(from);
    date.setDate(date.getDate() + index);
    const offset = Math.round((date.getTime() - today.getTime()) / 86400000);
    const isPast = offset < 0;
    const isBooked = venue.bookedOffsets.includes(offset);
    const withinNotice = offset * 24 < venue.minNoticeHours;
    return {
      date,
      offset,
      isAvailable: !isPast && !isBooked && !withinNotice,
      reason: isPast ? 'past' : isBooked ? 'booked' : withinNotice ? 'min_notice' : null,
    };
  });
}

// ── Reservas y panel (demostración) ──────────────────────────────────────────

export interface BookingDemo {
  id: string;
  folio: string;
  venueId: string;
  state: 'CONFIRMED' | 'COMPLETED' | 'CANCELLED_BY_CLIENT' | 'REFUNDED' | 'PENDING_PAYMENT';
  startAt: string;
  endAt: string;
  guests: number;
  totalCents: number;
  paidCents: number;
  canReview: boolean;
}

export function demoBookings(): BookingDemo[] {
  const base = new Date();
  base.setHours(15, 0, 0, 0);
  const at = (days: number, hours = 0) => {
    const date = new Date(base);
    date.setDate(date.getDate() + days);
    date.setHours(date.getHours() + hours);
    return date.toISOString();
  };
  return [
    {
      id: 'b-1',
      folio: 'QX-4F2A91',
      venueId: 'palapa-xcambo',
      state: 'CONFIRMED',
      startAt: at(12),
      endAt: at(12, 11),
      guests: 120,
      totalCents: 1_166_250,
      paidCents: 349_875,
      canReview: false,
    },
    {
      id: 'b-2',
      folio: 'QX-9B7C40',
      venueId: 'quinta-alcantara',
      state: 'COMPLETED',
      startAt: at(-52),
      endAt: at(-52, 11),
      guests: 180,
      totalCents: 2_890_000,
      paidCents: 2_890_000,
      canReview: true,
    },
    {
      id: 'b-3',
      folio: 'QX-1D5E88',
      venueId: 'salon-casa-nube',
      state: 'COMPLETED',
      startAt: at(-120),
      endAt: at(-120, 6),
      guests: 95,
      totalCents: 1_742_000,
      paidCents: 1_742_000,
      canReview: true,
    },
    {
      id: 'b-4',
      folio: 'QX-7A3F12',
      venueId: 'palapa-el-faro',
      state: 'CANCELLED_BY_CLIENT',
      startAt: at(-180),
      endAt: at(-180, 8),
      guests: 60,
      totalCents: 812_000,
      paidCents: 812_000,
      canReview: false,
    },
  ];
}

export interface ProviderTask {
  kind: 'payouts' | 'verification' | 'requests';
  label: string;
  severity: 'blocking' | 'action' | 'info';
}

export function demoProviderDashboard() {
  return {
    providerName: 'Palapa Xcambó',
    period: 'Octubre 2026',
    occupancyRate: 0.78,
    revenueCents: 8_420_000,
    confirmedBookings: 6,
    upcomingEvents: 3,
    avgTicketCents: 1_403_333,
    rating: 4.9,
    cancellations: 1,
    nextPayoutCents: 5_894_000,
    nextPayoutDate: '2026-11-05',
    pendingTasks: [
      {
        kind: 'verification',
        label: 'Falta tu constancia de situación fiscal',
        severity: 'info',
      },
      {
        kind: 'requests',
        label: '2 solicitudes por responder',
        severity: 'action',
      },
    ] as ProviderTask[],
    occupancyByWeekday: [0.38, 0.54, 0.92, 0.78, 0.46, 1, 0.62],
    events: [
      { folio: 'QX-4F2A91', kind: 'XV años · familia Pech', dayOffset: 12, guests: 120, amountCents: 1_166_250, status: 'Pagado' },
      { folio: 'QX-8C1B07', kind: 'Boda · M. y J. Uc', dayOffset: 19, guests: 90, amountCents: 1_430_000, status: 'Anticipo' },
      { folio: 'QX-2E6D55', kind: 'Cumpleaños · R. Coss', dayOffset: 26, guests: 60, amountCents: 980_000, status: 'Anticipo' },
    ],
  };
}

export const COUPONS: Record<string, { kind: 'PERCENT' | 'FIXED'; value: number; note: string }> = {
  FIESTA10: { kind: 'PERCENT', value: 10, note: '10% de descuento en el subtotal' },
  XCAMBOCASH: { kind: 'FIXED', value: 50_000, note: '$500 de descuento' },
  TEMPORADA: { kind: 'PERCENT', value: 15, note: '15% de temporada baja' },
};
