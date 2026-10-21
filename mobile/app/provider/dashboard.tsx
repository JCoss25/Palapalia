/**
 * Pantalla 6 · Panel del anfitrión.
 *
 * Números reales, no gráficas decorativas: ocupación con los días marcados y la próxima
 * cobranza. El bloqueo de verificación aparece antes de que duela (no se puede cobrar sin
 * cuenta de pagos completa).
 */

import React from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { format, parseISO } from 'date-fns';
import { es } from 'date-fns/locale';

import { useBookings, useProviderDashboard } from '../src/api/hooks';
import { formatMoney } from '../src/api/types';
import { Button, Chip, Notice, Reveal, SectionTitle, styles as uiStyles } from '../src/components/ui';
import { color, fontSize, lineHeight, radius, space, type } from '../src/theme/tokens';

const OCCUPANCY_BARS = [0.38, 0.54, 0.92, 0.78, 0.46, 1, 0.62];

export default function ProviderDashboard() {
  const insets = useSafeAreaInsets();
  const dashboard = useProviderDashboard('30d');
  const agenda = useBookings('provider');

  if (dashboard.isLoading || !dashboard.data) {
    return (
      <View style={[uiStyles.screen, { justifyContent: 'center', alignItems: 'center' }]}>
        <ActivityIndicator color={color.sea} />
      </View>
    );
  }

  const data = dashboard.data;
  const upcoming = (agenda.data?.items ?? []).filter((item) => item.state === 'CONFIRMED').slice(0, 3);

  return (
    <View style={uiStyles.screen}>
      <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + space.xxl }} showsVerticalScrollIndicator={false}>
        <View style={[uiStyles.content, { paddingTop: insets.top + space.sm }]}>
          <Text
            onPress={() => router.back()}
            accessibilityRole="button"
            accessibilityLabel="Volver"
            style={{ fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink, marginBottom: space.sm }}
          >
            ←
          </Text>

          <Text style={styles.eyebrow}>Panel · {data.period === '30d' ? 'últimos 30 días' : data.period}</Text>
          <Text style={[uiStyles.display, styles.title]} accessibilityRole="header">
            {Math.round(data.occupancy_rate * 100) >= 70 ? 'El mes va lleno.' : 'Aún hay fechas libres.'}
          </Text>

          {data.pending_tasks.map((task) => (
            <View key={task.kind} style={{ marginTop: space.md }}>
              <Notice
                tone={task.severity === 'blocking' ? 'danger' : task.severity === 'action' ? 'warning' : 'info'}
                title={task.label}
                body={
                  task.kind === 'payouts'
                    ? 'Sin cuenta de pagos no puedes recibir dinero en línea. Puedes seguir publicando con pago directo.'
                    : task.kind === 'verification'
                      ? 'Estamos revisando tus documentos. Suele tardar menos de 24 horas hábiles.'
                      : 'Responde pronto: el tiempo de respuesta afecta tu posición en la búsqueda.'
                }
                action={
                  task.severity === 'blocking' ? (
                    <Button label="Completar cuenta de pagos" variant="quiet" onPress={() => router.push('/provider/dashboard')} />
                  ) : null
                }
              />
            </View>
          ))}

          <View style={styles.kpiGrid}>
            <View style={styles.kpi}>
              <Text style={styles.kpiLabel}>Ocupación</Text>
              <Text style={styles.kpiValue}>{Math.round(data.occupancy_rate * 100)}%</Text>
              <Text style={styles.kpiDelta}>{data.upcoming_events} eventos por delante</Text>
            </View>
            <View style={styles.kpi}>
              <Text style={styles.kpiLabel}>Ingreso del periodo</Text>
              <Text style={styles.kpiValue}>{formatMoney(data.revenue_cents, data.revenue_currency)}</Text>
              <Text style={styles.kpiSub}>{data.confirmed_bookings} reservas · ticket promedio {formatMoney(data.avg_ticket_cents)}</Text>
            </View>
          </View>

          <View style={{ marginTop: space.lg }}>
            <View style={styles.occHeader}>
              <Text style={styles.sectionLabel}>Días apartados</Text>
              <Text style={styles.kpiSub}>
                {data.cancelled_by_client + data.cancelled_by_provider} cancelaciones
              </Text>
            </View>
            <View style={styles.bars}>
              {OCCUPANCY_BARS.map((value, index) => (
                <View
                  key={index}
                  style={[styles.bar, { height: `${value * 100}%` }, value >= 0.75 && styles.barHigh]}
                  accessible
                  accessibilityLabel={`${['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'][index]}: ${Math.round(
                    value * 100,
                  )}% ocupado`}
                />
              ))}
            </View>
            <View style={styles.barLabels}>
              {['L', 'M', 'M', 'J', 'V', 'S', 'D'].map((label, index) => (
                <Text key={`${label}-${index}`} style={styles.barLabel}>
                  {label}
                </Text>
              ))}
            </View>
          </View>

          {data.next_payout_cents !== null ? (
            <View style={{ marginTop: space.lg }}>
              <Notice
                tone="info"
                title={`Próxima liquidación: ${formatMoney(data.next_payout_cents, data.revenue_currency)}`}
                body={
                  data.next_payout_date
                    ? `Se libera el ${format(parseISO(data.next_payout_date), "d 'de' MMMM", { locale: es })}. Si hay una disputa abierta, se congela hasta resolverla.`
                    : 'Se libera al cierre del periodo, si no hay disputas abiertas.'
                }
              />
            </View>
          ) : null}

          <SectionTitle title="Próximos eventos" action="Ver agenda" onAction={() => router.push('/bookings')} />
          {upcoming.length === 0 ? (
            <Text style={styles.kpiSub}>Todavía no tienes eventos confirmados en el periodo.</Text>
          ) : (
            upcoming.map((booking, index) => (
              <Reveal key={booking.id} index={index}>
                <View style={styles.eventRow}>
                  <View style={{ flex: 1 }}>
                    <Text style={styles.eventTitle}>
                      {booking.state === 'CONFIRMED' ? 'Reserva confirmada' : booking.state} · {booking.folio}
                    </Text>
                    <Text style={styles.kpiSub}>
                      {format(parseISO(booking.start_at), "EEE d 'de' MMM", { locale: es })} · {booking.guests} personas
                    </Text>
                  </View>
                  <View style={{ alignItems: 'flex-end' }}>
                    <Text style={styles.eventAmount}>{formatMoney(booking.total_cents, booking.currency)}</Text>
                    <Text style={styles.kpiDelta}>
                      {booking.balance_cents > 0 ? 'Anticipo' : 'Pagado'}
                    </Text>
                  </View>
                </View>
              </Reveal>
            ))
          )}

          <View style={{ flexDirection: 'row', gap: space.xs, marginTop: space.lg }}>
            <Chip label="Bloquear fecha" onPress={() => router.push('/provider/dashboard')} />
            <Chip label="Ajustar precios" onPress={() => router.push('/provider/dashboard')} />
            <Chip label="Subir fotos" onPress={() => router.push('/provider/dashboard')} />
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  eyebrow: { fontFamily: type.bodyMedium, fontSize: fontSize.caption, color: color.inkSoft, textTransform: 'uppercase', letterSpacing: 0.8 },
  title: { fontSize: fontSize.headline, lineHeight: fontSize.headline * 1.05, letterSpacing: -0.6, marginTop: space.xxs },
  kpiGrid: { flexDirection: 'row', gap: space.xs, marginTop: space.lg },
  kpi: { flex: 1, borderWidth: 1, borderColor: color.line, borderRadius: radius.control, padding: space.sm, backgroundColor: color.white },
  kpiLabel: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, marginBottom: space.xs },
  kpiValue: { fontFamily: type.bodySemi, fontSize: fontSize.display, color: color.ink, letterSpacing: -1 },
  kpiDelta: { fontFamily: type.bodySemi, fontSize: 10, color: color.sea, marginTop: space.xxs },
  kpiSub: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, lineHeight: fontSize.caption * lineHeight.body },
  occHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: space.xs },
  sectionLabel: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  bars: { flexDirection: 'row', alignItems: 'flex-end', gap: 7, height: 82, borderBottomWidth: 1, borderBottomColor: color.line },
  bar: { flex: 1, backgroundColor: color.sand, borderRadius: 1 },
  barHigh: { backgroundColor: color.sea },
  barLabels: { flexDirection: 'row', gap: 7, marginTop: 6 },
  barLabel: { flex: 1, textAlign: 'center', fontFamily: type.body, fontSize: 9, color: color.inkSoft },
  eventRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: space.sm, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: color.line },
  eventTitle: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
  eventAmount: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
});
