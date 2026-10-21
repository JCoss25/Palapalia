/**
 * Pantalla 2 · Explorar (home + búsqueda).
 *
 * La fecha vive arriba, no en un filtro escondido: es la primera decisión. Cada tarjeta
 * responde «¿está libre mi día?» antes que el precio. Se muestran tres módulos distintos
 * (tarjeta grande, fila compacta, enlace al listado completo) en vez de repetir la misma tarjeta.
 */

import React, { useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { addDays, format, parseISO } from 'date-fns';
import { es } from 'date-fns/locale';

import { useHome, useVenueSearch } from '../src/api/hooks';
import type { VenueSummary } from '../src/api/types';
import { VenueCard, VenueRow } from '../src/components/cards';
import { Button, Chip, EmptyState, Notice, Reveal, SectionTitle, styles as uiStyles } from '../src/components/ui';
import { color, fontSize, lineHeight, radius, space, type } from '../src/theme/tokens';
import { bookingDraftStore } from '../src/store/session';

const DATE_OPTIONS = 5;

export default function Explore() {
  const insets = useSafeAreaInsets();
  const [selectedDate, setSelectedDate] = useState(() => addDays(new Date(), 1));
  const [category, setCategory] = useState<string | undefined>(undefined);
  const [city, setCity] = useState<string | undefined>(undefined);

  const dateFrom = useMemo(() => format(selectedDate, 'yyyy-MM-dd'), [selectedDate]);
  const dateTo = useMemo(() => format(addDays(selectedDate, 1), 'yyyy-MM-dd'), [selectedDate]);

  const search = useVenueSearch({ date_from: dateFrom, date_to: dateTo, category: category ? [category] : [], city });
  const home = useHome(city);

  const dates = useMemo(
    () => Array.from({ length: DATE_OPTIONS }, (_, index) => addDays(new Date(), index + 1)),
    [],
  );

  const results = search.data?.items ?? [];
  const [primary, ...rest] = results;

  return (
    <View style={[uiStyles.screen, { paddingTop: insets.top }]}>
      <ScrollView
        contentContainerStyle={{ paddingBottom: insets.bottom + space.xxl }}
        showsVerticalScrollIndicator={false}
      >
        <View style={uiStyles.content}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <View>
              <Text style={styles.place}>{city ?? 'Progreso, Yucatán'}</Text>
              <Text style={[uiStyles.display, styles.headline]} accessibilityRole="header">
                ¿Qué vas{'\n'}a celebrar?
              </Text>
            </View>
            <View style={styles.avatar}>
              <Text style={styles.avatarText}>MC</Text>
            </View>
          </View>

          <View style={styles.searchBar}>
            <Text style={styles.searchPlaceholder}>Palapa, quinta, salón…</Text>
            <View style={styles.searchAction}>
              <Text style={{ color: color.white, fontFamily: type.bodySemi }}>→</Text>
            </View>
          </View>
        </View>

        {/* La fecha primero */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: space.lg, gap: space.xs, paddingTop: space.md }}
        >
          {dates.map((date) => {
            const isSelected = format(date, 'yyyy-MM-dd') === dateFrom;
            return (
              <View
                key={date.toISOString()}
                style={[styles.dateCell, isSelected && styles.dateCellSelected]}
                accessible
                accessibilityLabel={`${format(date, "EEEE d 'de' MMMM", { locale: es })}${isSelected ? ', seleccionado' : ''}`}
                onStartShouldSetResponder={() => true}
                onResponderRelease={() => setSelectedDate(date)}
              >
                <Text style={[styles.dateWeekday, isSelected && styles.dateTextSelected]}>
                  {format(date, 'EEE', { locale: es })}
                </Text>
                <Text style={[styles.dateNumber, isSelected && styles.dateTextSelected]}>{format(date, 'd')}</Text>
              </View>
            );
          })}
        </ScrollView>

        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ paddingHorizontal: space.lg, gap: space.xs, paddingTop: space.md, paddingBottom: space.md }}
        >
          <Chip label="Todos" selected={!category} onPress={() => setCategory(undefined)} />
          <Chip label="Palapa" selected={category === 'palapa'} onPress={() => setCategory('palapa')} />
          <Chip label="Salón" selected={category === 'salon'} onPress={() => setCategory('salon')} />
          <Chip label="Quinta" selected={category === 'quinta'} onPress={() => setCategory('quinta')} />
        </ScrollView>

        <View style={uiStyles.content}>
          {search.isError ? (
            <Notice
              tone="danger"
              title="No pudimos cargar los espacios"
              body="Revisa tu conexión. Tu apartado, si ya lo hiciste, sigue activo en el servidor."
              action={<Button label="Reintentar" variant="quiet" onPress={() => void search.refetch()} />}
            />
          ) : search.isLoading ? (
            <ActivityIndicator color={color.sea} style={{ marginTop: space.xl }} />
          ) : !primary ? (
            <EmptyState
              title={`Nada libre el ${format(selectedDate, "d 'de' MMMM", { locale: es })}`}
              body="Prueba otro día o quita filtros. También puedes avisarnos y te buscamos alternativas."
              action={<Button label="Ver otra fecha" variant="quiet" onPress={() => setSelectedDate(addDays(selectedDate, 1))} />}
            />
          ) : (
            <Reveal index={0}>
              <VenueCard
                venue={primary}
                onPress={() => {
                  bookingDraftStore.getState().setDraft({
                    venueId: primary.id,
                    startAt: parseISO(`${dateFrom}T15:00:00`).toISOString(),
                  });
                  router.push(`/venue/${primary.id}?date=${dateFrom}`);
                }}
              />
            </Reveal>
          )}

          {rest.length > 0 ? (
            <Reveal index={1}>
              <SectionTitle title={`También libres el ${format(selectedDate, 'd', { locale: es })} de ${format(selectedDate, 'MMMM', { locale: es })}`} action={`${rest.length} espacios`} />
              {rest.slice(0, 3).map((venue: VenueSummary) => (
                <VenueRow key={venue.id} venue={venue} onPress={() => router.push(`/venue/${venue.id}?date=${dateFrom}`)} />
              ))}
            </Reveal>
          ) : null}
        </View>

        {home.data?.length ? (
          <View style={[uiStyles.content, { marginTop: space.xl }]}>
            {home.data.slice(0, 2).map((section, index) => (
              <Reveal key={section.section} index={index + 2} style={{ marginBottom: space.lg }}>
                <SectionTitle title={section.title} />
                {section.disclosure ? (
                  <Text style={styles.disclosure}>{section.disclosure}</Text>
                ) : (
                  section.subtitle ? <Text style={styles.disclosure}>{section.subtitle}</Text> : null
                )}
                {section.venues.slice(0, 2).map((venue) => (
                  <VenueRow key={venue.id} venue={venue} onPress={() => router.push(`/venue/${venue.id}`)} />
                ))}
              </Reveal>
            ))}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  headline: { fontSize: fontSize.headline, lineHeight: fontSize.headline * 1.06, letterSpacing: -0.6 },
  place: { fontFamily: type.body, fontSize: fontSize.caption, color: color.sea, marginBottom: space.xxs },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: radius.pill,
    backgroundColor: color.sand,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: color.line,
  },
  avatarText: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.ink },
  searchBar: {
    marginTop: space.md,
    height: 50,
    borderRadius: radius.control,
    backgroundColor: color.white,
    borderWidth: 1,
    borderColor: color.line,
    flexDirection: 'row',
    alignItems: 'center',
    paddingLeft: space.sm,
    paddingRight: 6,
    gap: space.xs,
  },
  searchPlaceholder: { flex: 1, fontFamily: type.body, fontSize: fontSize.body, color: color.inkFaint },
  searchAction: {
    width: 32,
    height: 32,
    borderRadius: radius.media + 1,
    backgroundColor: color.sea,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dateCell: {
    minWidth: 58,
    height: 68,
    borderRadius: radius.control - 1,
    borderWidth: 1,
    borderColor: color.line,
    backgroundColor: color.white,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
  },
  dateCellSelected: { backgroundColor: color.sea, borderColor: color.sea },
  dateWeekday: { fontFamily: type.bodyMedium, fontSize: 10, color: color.inkSoft, textTransform: 'uppercase' },
  dateNumber: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  dateTextSelected: { color: color.white },
  disclosure: {
    fontFamily: type.body,
    fontSize: fontSize.caption,
    color: color.inkSoft,
    marginTop: -space.xs,
    marginBottom: space.sm,
    lineHeight: fontSize.caption * lineHeight.body,
  },
});
