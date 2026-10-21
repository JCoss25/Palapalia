/** Tarjetas del catálogo y la pieza central: el recibo de apartado con cuenta regresiva. */

import React, { useEffect, useState } from 'react';
import { Image } from 'expo-image';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { format, parseISO } from 'date-fns';
import { es } from 'date-fns/locale';

import { color, fontSize, lineHeight, radius, space, type } from '../theme/tokens';
import { formatCountdown, formatMoney, type Hold, type Quote, type VenueSummary } from '../api/types';
import { Button, PressableScale, StarRating } from './ui';

/** Foto con proporción fija: mismo recorte en todo el catálogo (27·regla de media frame). */
export function MediaFrame({
  uri,
  height = 198,
  accessibilityLabel,
  children,
}: {
  uri: string | null;
  height?: number;
  accessibilityLabel: string;
  children?: React.ReactNode;
}) {
  return (
    <View style={{ height, borderRadius: radius.media, overflow: 'hidden', backgroundColor: color.sand }}>
      {uri ? (
        <Image
          source={{ uri }}
          style={StyleSheet.absoluteFill}
          contentFit="cover"
          transition={180}
          accessibilityLabel={accessibilityLabel}
        />
      ) : null}
      {children}
    </View>
  );
}

export function VenueCard({ venue, onPress }: { venue: VenueSummary; onPress: () => void }) {
  const available = venue.is_available;
  return (
    <PressableScale
      accessibilityRole="button"
      accessibilityLabel={`${venue.name}, ${venue.city}. Hasta ${venue.capacity_max} personas. Desde ${formatMoney(
        venue.price_from_cents ?? venue.base_price_cents ?? 0,
        venue.currency,
      )} por día.`}
      onPress={onPress}
      style={{ marginBottom: space.lg }}
    >
      <MediaFrame
        uri={venue.cover_url}
        accessibilityLabel={`Foto de ${venue.name}`}
      >
        {venue.sponsored ? (
          <View style={styles.tagSponsored}>
            <Text style={styles.tagSponsoredText}>Patrocinado</Text>
          </View>
        ) : venue.instant_book ? (
          <View style={styles.tag}>
            <Text style={styles.tagText}>Reserva inmediata</Text>
          </View>
        ) : null}
      </MediaFrame>

      <View style={{ flexDirection: 'row', justifyContent: 'space-between', paddingTop: space.xs, gap: space.sm }}>
        <View style={{ flex: 1 }}>
          <Text style={styles.cardTitle}>{venue.name}</Text>
          <Text style={styles.cardMeta}>
            {venue.city}, {venue.state} · {venue.capacity_max} personas
          </Text>
          {available === true ? (
            <Text style={styles.available}>✓ Libre en tu fecha</Text>
          ) : available === false ? (
            <Text style={styles.unavailable}>Ocupado en tu fecha</Text>
          ) : null}
        </View>
        <StarRating value={venue.rating_avg} count={venue.rating_count} />
      </View>

      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: space.xxs }}>
        <Text style={styles.cardMeta}>Desde</Text>
        <Text style={styles.price}>
          {formatMoney(venue.price_from_cents ?? venue.base_price_cents ?? 0, venue.currency)}
          <Text style={styles.cardMeta}> por día</Text>
        </Text>
      </View>
    </PressableScale>
  );
}

/** Fila compacta: segunda jerarquía del listado, sin repetir la tarjeta grande. */
export function VenueRow({ venue, onPress }: { venue: VenueSummary; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${venue.name}, ${venue.city}`}
      onPress={onPress}
      style={styles.row}
    >
      <MediaFrame uri={venue.cover_url} height={70} accessibilityLabel={`Foto de ${venue.name}`} />
      <View style={{ flex: 1 }}>
        <Text style={styles.rowTitle}>{venue.name}</Text>
        <Text style={styles.cardMeta}>
          {venue.city} · {venue.capacity_max} personas
        </Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={styles.rowPrice}>{formatMoney(venue.base_price_cents ?? 0, venue.currency)}</Text>
        <Text style={styles.cardMeta}>por día</Text>
      </View>
    </Pressable>
  );
}

/**
 * Recibo de apartado: folio, fecha, desglose y reloj real.
 *
 * El reloj se calcula en el cliente a partir de `expires_at` (no se confía en un contador
 * propio) y se detiene en 00:00 mostrando el estado real del servidor.
 */
export function HoldTicket({
  hold,
  quote,
  onExtend,
  extending = false,
}: {
  hold: Hold;
  quote?: Quote;
  onExtend?: () => void;
  extending?: boolean;
}) {
  const [remaining, setRemaining] = useState(hold.seconds_remaining);
  const expired = remaining <= 0;

  useEffect(() => {
    setRemaining(Math.max(Math.floor((parseISO(hold.expires_at).getTime() - Date.now()) / 1000), 0));
    const interval = setInterval(() => {
      setRemaining((current) => (current <= 1 ? 0 : current - 1));
    }, 1000);
    return () => clearInterval(interval);
  }, [hold.expires_at]);

  const lines = quote?.lines ?? [];
  const timezone = hold.venue_timezone;

  return (
    <View style={styles.ticket}>
      <View style={{ padding: space.md }}>
        <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
          <MediaFrame uri={hold.venue_cover_url} height={52} accessibilityLabel={`Foto de ${hold.venue_name ?? 'el espacio'}`} />
          <View style={{ flex: 1 }}>
            <Text style={styles.cardTitle}>{hold.venue_name ?? 'Espacio apartado'}</Text>
            <Text style={styles.cardMeta}>Folio {hold.folio} · Apartado con precio congelado</Text>
          </View>
        </View>

        <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: space.md }}>
          <View>
            <Text style={styles.ticketDate}>
              {format(parseISO(hold.start_at), "EEE d 'de' MMMM", { locale: es })}
            </Text>
            <Text style={styles.cardMeta}>
              {format(parseISO(hold.start_at), 'HH:mm')} → {format(parseISO(hold.end_at), 'HH:mm')} · hora de{' '}
              {timezone}
            </Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text
              style={[styles.countdown, expired && { color: color.danger }]}
              accessibilityLabel={`Quedan ${Math.floor(remaining / 60)} minutos para pagar`}
            >
              {formatCountdown(remaining)}
            </Text>
            <Text style={styles.cardMeta}>{expired ? 'apartado vencido' : 'para pagar'}</Text>
          </View>
        </View>
      </View>

      <View style={styles.perforation}>
        <View style={styles.notchLeft} />
        <View style={styles.notchRight} />
      </View>

      <View style={{ padding: space.md }}>
        {lines.length === 0 ? (
          <Text style={styles.cardMeta}>Calculando desglose…</Text>
        ) : (
          lines.map((line) => (
            <View key={`${line.concept}-${line.amount_cents}`} style={styles.line}>
              <Text style={[styles.lineLabel, line.is_discount && { color: color.sea }]}>
                {line.concept}
                {line.detail ? ` · ${line.detail}` : ''}
              </Text>
              <Text style={[styles.lineAmount, line.is_discount && { color: color.sea }]}>
                {line.is_discount ? '−' : ''}
                {formatMoney(line.amount_cents, quote?.currency ?? hold.currency)}
              </Text>
            </View>
          ))
        )}
        <View style={[styles.line, styles.lineTotal]}>
          <Text style={styles.totalLabel}>Total hoy</Text>
          <Text style={styles.totalAmount}>
            {formatMoney(quote?.deposit_cents ?? hold.deposit_cents, hold.currency)}
          </Text>
        </View>
      </View>

      {expired && onExtend ? (
        <View style={{ paddingHorizontal: space.md, paddingBottom: space.md }}>
          <Button
            label={extending ? 'Liberando…' : 'Vencer y buscar otra fecha'}
            variant="quiet"
            onPress={onExtend}
            loading={extending}
          />
        </View>
      ) : null}
    </View>
  );
}

/** Bloque legal/política: se muestra antes de pagar, no escondido en un enlace. */
export function PolicyNote({ text }: { text: string }) {
  return (
    <View style={styles.policy}>
      <Text style={styles.policyText}>{text}</Text>
      <Pressable onPress={() => void Linking.openURL('https://quinta.mx/politica-cancelacion')}>
        <Text style={{ color: color.sea, fontFamily: type.bodySemi, fontSize: fontSize.caption, marginTop: space.xxs }}>
          Leer la política completa
        </Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  cardTitle: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  cardMeta: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, marginTop: 2 },
  price: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  available: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.sea, marginTop: space.xxs },
  unavailable: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.danger, marginTop: space.xxs },
  tag: {
    position: 'absolute',
    top: space.xs,
    left: space.xs,
    backgroundColor: 'rgba(251,250,246,0.94)',
    paddingHorizontal: space.xs,
    paddingVertical: 5,
    borderRadius: radius.media,
  },
  tagText: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.ink },
  tagSponsored: {
    position: 'absolute',
    top: space.xs,
    left: space.xs,
    backgroundColor: 'rgba(14,95,82,0.94)',
    paddingHorizontal: space.xs,
    paddingVertical: 5,
    borderRadius: radius.media,
  },
  tagSponsoredText: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.white },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
    paddingVertical: space.xs,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: color.line,
  },
  rowTitle: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
  rowPrice: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
  ticket: {
    borderWidth: 1,
    borderColor: color.line,
    borderRadius: radius.control,
    backgroundColor: color.white,
    overflow: 'hidden',
  },
  ticketDate: { fontFamily: type.display, fontSize: fontSize.title, color: color.ink, letterSpacing: -0.4 },
  countdown: { fontFamily: type.bodySemi, fontSize: fontSize.title, color: color.sea, fontVariant: ['tabular-nums'] },
  perforation: { height: 0, borderTopWidth: 1, borderStyle: 'dashed', borderTopColor: color.lineStrong, marginHorizontal: space.md },
  notchLeft: {
    position: 'absolute',
    left: -space.md - 6,
    top: -9,
    width: 18,
    height: 18,
    borderRadius: radius.pill,
    backgroundColor: color.paper,
    borderWidth: 1,
    borderColor: color.line,
  },
  notchRight: {
    position: 'absolute',
    right: -space.md - 6,
    top: -9,
    width: 18,
    height: 18,
    borderRadius: radius.pill,
    backgroundColor: color.paper,
    borderWidth: 1,
    borderColor: color.line,
  },
  line: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 6, gap: space.sm },
  lineLabel: { fontFamily: type.body, fontSize: fontSize.small, color: color.inkSoft, flex: 1 },
  lineAmount: { fontFamily: type.bodyMedium, fontSize: fontSize.small, color: color.ink, fontVariant: ['tabular-nums'] },
  lineTotal: {
    borderTopWidth: 1,
    borderTopColor: color.line,
    marginTop: space.xs,
    paddingTop: space.sm,
    alignItems: 'baseline',
  },
  totalLabel: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  totalAmount: { fontFamily: type.bodySemi, fontSize: fontSize.title, color: color.ink, letterSpacing: -0.5 },
  policy: {
    borderLeftWidth: 2,
    borderLeftColor: color.sea,
    paddingLeft: space.sm,
    marginTop: space.md,
  },
  policyText: {
    fontFamily: type.body,
    fontSize: fontSize.caption,
    color: color.inkSoft,
    lineHeight: fontSize.caption * lineHeight.body,
  },
});
