/**
 * Pantalla 3 · Detalle del espacio.
 *
 * La disponibilidad es el bloque principal, no una pestaña. El CTA nombra la fecha:
 * no existen dos reservas iguales, así que el botón dice «Apartar el 17 de oct».
 */

import React, { useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { addDays, format, parseISO } from 'date-fns';
import { es } from 'date-fns/locale';

import { useAvailability, useCreateHold, useQuote, useVenue } from '../src/api/hooks';
import { ApiError } from '../src/api/client';
import { formatMoney } from '../src/api/types';
import { MediaFrame, PolicyNote } from '../src/components/cards';
import { Button, Chip, Notice, Reveal, SectionTitle, StarRating, styles as uiStyles } from '../src/components/ui';
import { color, fontSize, lineHeight, radius, space, type } from '../src/theme/tokens';
import { bookingDraftStore } from '../src/store/session';

export default function VenueDetailScreen() {
  const { id, date } = useLocalSearchParams<{ id: string; date?: string }>();
  const insets = useSafeAreaInsets();
  const venue = useVenue(id);

  const initialDate = date ? parseISO(date) : addDays(new Date(), 1);
  const [selectedDate, setSelectedDate] = useState(initialDate);
  const [guests, setGuests] = useState(80);
  const [extras, setExtras] = useState<string[]>([]);
  const [holdError, setHoldError] = useState<ApiError | null>(null);

  const from = format(selectedDate, 'yyyy-MM-dd');
  const to = format(addDays(selectedDate, 6), 'yyyy-MM-dd');
  const availability = useAvailability(id, from, to);

  const window = useMemo(() => {
    const day = availability.data?.days.find((item) => item.date === from);
    const slot = day?.slots.find((item) => item.is_available) ?? day?.slots[0];
    if (!slot) return null;
    return { start_at: slot.start_at, end_at: slot.end_at };
  }, [availability.data, from]);

  const quote = useQuote(id, window ? { ...window, guests, extras } : null);
  const createHold = useCreateHold();

  if (venue.isLoading) {
    return (
      <View style={[uiStyles.screen, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator color={color.sea} />
      </View>
    );
  }

  if (venue.isError || !venue.data) {
    return (
      <View style={[uiStyles.screen, uiStyles.content, { paddingTop: insets.top + space.xl }]}>
        <Notice
          tone="danger"
          title="No pudimos abrir este espacio"
          body="Puede que ya no esté publicado. Vuelve al buscador y elige otro."
          action={<Button label="Volver al buscador" variant="quiet" onPress={() => router.replace('/')} />}
        />
      </View>
    );
  }

  const data = venue.data;
  const cover = data.media[0]?.url ?? data.cover_url;
  const dayInfo = availability.data?.days.find((item) => item.date === from);

  const handleHold = async () => {
    if (!window) return;
    setHoldError(null);
    try {
      const hold = await createHold.mutateAsync({
        venue_id: data.id,
        start_at: window.start_at,
        end_at: window.end_at,
        guests,
        slot_kind: data.slot_kind_default,
        extras,
        quote_total_cents: quote.data?.total_cents,
      });
      bookingDraftStore.getState().setDraft({ venueId: data.id, startAt: window.start_at, endAt: window.end_at, guests, extras });
      router.push(`/checkout/${hold.id}`);
    } catch (error) {
      setHoldError(error as ApiError);
    }
  };

  return (
    <View style={uiStyles.screen}>
      <ScrollView contentContainerStyle={{ paddingBottom: 120 + insets.bottom }} showsVerticalScrollIndicator={false}>
        <MediaFrame uri={cover} height={318} accessibilityLabel={`Foto principal de ${data.name}`}>
          <View style={styles.backButton}>
            <Text
              onPress={() => router.back()}
              accessibilityRole="button"
              accessibilityLabel="Volver"
              style={{ fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink }}
            >
              ←
            </Text>
          </View>
          <View style={styles.photoCount}>
            <Text style={{ color: color.white, fontFamily: type.bodyMedium, fontSize: fontSize.caption }}>
              1 / {Math.max(data.media.length, 1)}
            </Text>
          </View>
        </MediaFrame>

        <View style={[uiStyles.content, { marginTop: -18, backgroundColor: color.paper, borderTopLeftRadius: radius.sheet, borderTopRightRadius: radius.sheet, paddingTop: space.lg }]}>
          <Text style={styles.kicker}>
            {data.event_types[0]?.name ?? 'Eventos'} · {data.city}, {data.state}
          </Text>
          <Text style={[uiStyles.display, styles.title]} accessibilityRole="header">
            {data.name}
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: space.xs, marginTop: space.xxs }}>
            <StarRating value={data.rating_avg} count={data.rating_count} />
            <Text style={styles.meta}>· mínimo {data.min_hours} h · buffer de {data.buffer_minutes} min incluido</Text>
          </View>

          <View style={styles.facts}>
            <Fact label="Capacidad" value={`hasta ${data.capacity_max}`} />
            <Fact label="Desde" value={formatMoney(data.base_price_cents ?? 0, data.currency)} />
            <Fact label="Anticipo" value={`${data.deposit_percent}%`} />
          </View>

          <SectionTitle title={`Disponibilidad · ${format(selectedDate, 'MMMM yyyy', { locale: es })}`} />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, paddingBottom: space.md }}>
            {(availability.data?.days ?? []).map((day) => {
              const isSelected = day.date === from;
              return (
                <View
                  key={day.date}
                  style={[styles.dayCell, isSelected && styles.dayCellSelected, !day.is_available && styles.dayCellOff]}
                  accessible
                  accessibilityLabel={`${format(parseISO(day.date), "EEEE d 'de' MMMM", { locale: es })}: ${
                    day.is_available ? 'disponible' : `no disponible (${day.reason_unavailable ?? 'ocupado'})`
                  }`}
                  onStartShouldSetResponder={() => true}
                  onResponderRelease={() => setSelectedDate(parseISO(day.date))}
                >
                  <Text style={[styles.dayWeekday, isSelected && { color: color.white }]}>
                    {format(parseISO(day.date), 'EEE', { locale: es })}
                  </Text>
                  <Text style={[styles.dayNumber, isSelected && { color: color.white }]}>
                    {format(parseISO(day.date), 'd')}
                  </Text>
                </View>
              );
            })}
          </ScrollView>

          {dayInfo && !dayInfo.is_available ? (
            <Notice
              tone="warning"
              title="Ese día no está libre"
              body={
                dayInfo.reason_unavailable === 'blackout'
                  ? 'El anfitrión cerró esa fecha. Prueba otro día del calendario.'
                  : 'Ya hay un evento ese día. Las fechas contiguas respetan el tiempo de limpieza.'
              }
            />
          ) : null}

          <SectionTitle title="Invitados" />
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: space.xs, paddingBottom: space.md }}>
            {[30, 50, 80, 120, 200].map((value) => (
              <Chip
                key={value}
                label={`${value} invitados`}
                selected={guests === value}
                onPress={() => setGuests(value)}
              />
            ))}
          </ScrollView>

          <SectionTitle title="Lo que incluye" />
          {(data.included_items.length ? data.included_items : ['Espacio habilitado y limpio', 'Montaje según horario acordado']).map(
            (item, index) => (
              <Reveal key={`${item}-${index}`} index={index}>
                <View style={styles.perk}>
                  <Text style={{ color: color.sea, fontFamily: type.bodySemi }}>✓</Text>
                  <Text style={styles.perkText}>{item}</Text>
                </View>
              </Reveal>
            ),
          )}

          {data.amenities.length ? (
            <>
              <SectionTitle title="Servicios del espacio" />
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: space.xs }}>
                {data.amenities.slice(0, 8).map((amenity) => (
                  <View key={amenity.id} style={styles.amenity}>
                    <Text style={styles.amenityText}>{amenity.name}</Text>
                  </View>
                ))}
              </View>
            </>
          ) : null}

          {data.house_rules ? (
            <>
              <SectionTitle title="Reglas de la casa" />
              <Text style={styles.rules}>{data.house_rules}</Text>
            </>
          ) : null}

          <PolicyNote
            text={`Cancelación escalonada: 100% hasta 30 días antes, 50% entre 7 y 30 días. Después de 7 días no hay reembolso. El anticipo del ${data.deposit_percent}% congela el precio mientras el apartado esté activo.`}
          />
        </View>
      </ScrollView>

      <View style={[styles.dock, { paddingBottom: insets.bottom + space.sm }]}>
        <View style={{ flex: 1 }}>
          <Text style={styles.dockPrice}>
            {quote.data ? formatMoney(quote.data.total_cents, quote.data.currency) : '—'}
          </Text>
          <Text style={styles.meta}>
            {quote.data ? `${quote.data.duration_hours} h · anticipo ${formatMoney(quote.data.deposit_cents)}` : 'Calculando…'}
          </Text>
        </View>
        <Button
          label={
            window
              ? `Apartar el ${format(parseISO(window.start_at), 'd \'de\' MMM', { locale: es })}`
              : 'Sin disponibilidad'
          }
          disabled={!window || !quote.data}
          loading={createHold.isPending}
          fullWidth={false}
          testID="venue-hold-cta"
          onPress={() => void handleHold()}
        />
      </View>

      {holdError ? (
        <View style={[styles.errorDock, { bottom: insets.bottom + 96 }]}>
          <Notice
            tone={holdError.code === 'VENUE_NOT_AVAILABLE' ? 'warning' : 'danger'}
            title={holdError.code === 'VENUE_NOT_AVAILABLE' ? 'Se ocupó hace un momento' : 'No se pudo apartar'}
            body={holdError.message}
            action={
              holdError.code === 'HOLD_LIMIT_REACHED' ? (
                <Button label="Ver mis reservas" variant="quiet" onPress={() => router.push('/bookings')} />
              ) : (
                <Button label="Elegir otro día" variant="quiet" onPress={() => setHoldError(null)} />
              )
            }
          />
        </View>
      ) : null}
    </View>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.fact}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={styles.factValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: fontSize.display, lineHeight: fontSize.display * 1.02, letterSpacing: -0.8, marginTop: space.xxs },
  kicker: { fontFamily: type.bodyMedium, fontSize: fontSize.caption, color: color.sea },
  meta: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft },
  backButton: {
    position: 'absolute',
    top: space.md,
    left: space.md,
    width: 36,
    height: 36,
    borderRadius: radius.pill,
    backgroundColor: 'rgba(251,250,246,0.94)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  photoCount: {
    position: 'absolute',
    right: space.md,
    bottom: space.md,
    backgroundColor: 'rgba(15,25,20,0.72)',
    paddingHorizontal: space.xs,
    paddingVertical: 5,
    borderRadius: radius.media,
  },
  facts: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: color.line,
    marginVertical: space.md,
  },
  fact: { flex: 1, paddingVertical: space.sm, borderRightWidth: StyleSheet.hairlineWidth, borderRightColor: color.line, paddingLeft: space.xs },
  factLabel: { fontFamily: type.body, fontSize: 10, color: color.inkSoft, textTransform: 'uppercase', letterSpacing: 0.6 },
  factValue: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink, marginTop: 2 },
  dayCell: {
    minWidth: 56,
    height: 58,
    borderRadius: radius.control - 1,
    borderWidth: 1,
    borderColor: color.line,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: color.white,
  },
  dayCellSelected: { backgroundColor: color.sea, borderColor: color.sea },
  dayCellOff: { backgroundColor: color.bone },
  dayWeekday: { fontFamily: type.body, fontSize: 10, color: color.inkSoft, textTransform: 'uppercase' },
  dayNumber: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
  perk: { flexDirection: 'row', gap: space.xs, alignItems: 'flex-start', marginBottom: space.xs },
  perkText: {
    flex: 1,
    fontFamily: type.body,
    fontSize: fontSize.small,
    color: color.ink,
    lineHeight: fontSize.small * lineHeight.body,
  },
  amenity: {
    paddingHorizontal: space.sm,
    paddingVertical: 6,
    borderRadius: radius.control,
    backgroundColor: color.bone,
  },
  amenityText: { fontFamily: type.body, fontSize: fontSize.caption, color: color.ink },
  rules: {
    fontFamily: type.body,
    fontSize: fontSize.small,
    color: color.inkSoft,
    lineHeight: fontSize.small * lineHeight.body,
  },
  dock: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.md,
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    backgroundColor: color.paper,
    borderTopWidth: 1,
    borderTopColor: color.line,
  },
  dockPrice: { fontFamily: type.bodySemi, fontSize: fontSize.title, color: color.ink, letterSpacing: -0.5 },
  errorDock: { position: 'absolute', left: space.lg, right: space.lg },
});
