/**
 * Tokens de diseño de Quinta.
 *
 * Design read: «un cuaderno de apartados junto al mar». Papel hueso, tinta pino y un solo
 * verde agua para la acción. La fecha manda: la primera decisión de cada pantalla es el día.
 *
 * Diales (taste-skill): VARIANCE 7 · MOTION 4 · DENSITY 4.
 * Reglas que se cumplen aquí: una sola familia tipográfica para UI + una display, radios
 * decididos una vez (2 para media, 4 para controles, 999 sólo para chips), cero degradados
 * salvo el scrim sobre foto, cero sombras salvo lo que de verdad flota.
 */

export const color = {
  bone: '#EFEBDF',
  paper: '#FBFAF6',
  ink: '#15201B',
  inkSoft: '#5D6660',
  inkFaint: '#8B938D',
  sea: '#0E5F52',
  seaDeep: '#0A3B34',
  seaWash: '#E4EDE8',
  sand: '#E3DCCB',
  line: 'rgba(21,32,27,0.14)',
  lineStrong: 'rgba(21,32,27,0.28)',
  amber: '#B4771F',
  warningBg: '#F6EDDC',
  warningInk: '#6A5637',
  danger: '#97402E',
  dangerBg: '#F4E7E3',
  overlay: 'rgba(11,23,19,0.94)',
  white: '#FFFFFF',
} as const;

export const space = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 20,
  xl: 28,
  xxl: 40,
  section: 56,
} as const;

export const radius = {
  media: 2,
  control: 4,
  sheet: 16,
  pill: 999,
} as const;

export const type = {
  display: 'Fraunces_600SemiBold',
  displayFallback: undefined,
  body: 'Inter_400Regular',
  bodyMedium: 'Inter_500Medium',
  bodySemi: 'Inter_600SemiBold',
} as const;

/** Escala tipográfica: 5 pasos visibles, sin tamaños intermedios decorativos. */
export const fontSize = {
  caption: 11,
  small: 13,
  body: 15,
  title: 20,
  headline: 26,
  display: 34,
  hero: 46,
} as const;

export const lineHeight = {
  tight: 1.02,
  title: 1.12,
  body: 1.5,
} as const;

export const motion = {
  /** Duraciones cortas: el movimiento informa, no entretiene (MOTION 4). */
  fast: 140,
  base: 220,
  slow: 320,
  /** Desplazamiento de entrada por sección */
  rise: 16,
  /** Retardo escalonado entre elementos de una lista */
  stagger: 70,
} as const;

export const hitSlop = { top: 8, bottom: 8, left: 8, right: 8 } as const;

/** Altura de controles táctiles: nunca por debajo de 44 pt (WCAG 2.2 AA / HIG). */
export const control = {
  minTouch: 44,
  button: 52,
  input: 50,
  chip: 34,
} as const;

export const shadow = {
  /** Sólo para lo que flota de verdad: hojas, diálogos, el botón flotante. */
  floating: {
    shadowColor: '#15201B',
    shadowOpacity: 0.18,
    shadowRadius: 20,
    shadowOffset: { width: 0, height: 8 },
    elevation: 6,
  },
} as const;

export const theme = { color, space, radius, type, fontSize, lineHeight, motion, control, shadow } as const;
export type Theme = typeof theme;
