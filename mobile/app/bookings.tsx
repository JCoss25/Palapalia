/**
 * Pantalla 5 · Mis reservas.
 *
 * La próxima reserva se lee como un pase: folio, cuenta regresiva y la siguiente acción.
 * Sin tarjetas idénticas en fila: la primera es un pase, el resto es historial compacto.
 */

import React, { useMemo, useState } from 'react';
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { differenceInCalendarDays, format, parseISO } from 'date-fns';
import { es } from 'date-fns/locale';

import { useBookings, useCancelBooking } from '../src/api/hooks';
import { ApiError } from '../src/api/client';
import { formatMoney, type BookingSummary } from '../src/api/types';
import { MediaFrame, PolicyNote } from '../src/components/cards';
import { Button, Chip, EmptyState, Notice, Reveal, SectionTitle, styles as uiStyles } from '../src/components/ui';
import { color, fontSize, lineHeight, radius, space, type } from '../src/theme/tokens';

type Tab = 'upcoming' | 'past' | 'cancelled';

const STATUS_LABEL: Record<string, { text: string; tone: 'ok' | 'done' | 'danger' }> = {
  CONFIRMED: { text: 'Confirmada', tone: 'ok' },
  PENDING_PAYMENT: { text: 'Falta pagar', tone: 'ok' },
  COMPLETED: { text: 'Completada', tone: 'done' },
  CANCELLED_BY_CLIENT: { text: 'Cancelada por ti', tone: 'danger' },
  CANCELLED_BY_PROVIDER: { text: 'Cancelada por el anfitrión', tone: 'danger' },
  NO_SHOW: { text: 'No asististe', tone: 'danger' },
  REFUNDED: { text: 'Reembolsada', tone: 'done' },
  DISPUTED: { text: 'En revisión', tone: 'danger' },
};

export default function MyBookings() {
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<Tab>('upcoming');
  const [error, setError] = useState<ApiError | null>(null);
  const bookings = useBookings('client');
  const cancel = useCancelBooking();

  const grouped = useMemo(() => {
    const items = bookings.data?.items ?? [];
    const now = new Date();
    return {
      upcoming: items.filter(
        (item) => parseISO(item.start_at) >= now && ['CONFIRMED', 'PENDING_PAYMENT', 'HOLD'].includes(item.state),
      ),
      past: items.filter((item) => item.state === 'COMPLETED' || item.state === 'NO_SHOW'),
      cancelled: items.filter((item) => item.state.startsWith('CANCELLED') || item.state === 'REFUNDED'),
    };
  }, [bookings.data]);

  const list = grouped[tab];
  const [next, ...history] = list;

  const handleCancel = async (booking: BookingSummary) => {
    setError(null);
    try {
      const result = await cancel.mutateAsync({
        booking_id: booking.id,
        reason: 'cambio de planes',
        reason_code: 'change_of_plans',
      });
      setError(
        new ApiError({
          code: 'REFUND_ISSUED',
          message: `Reserva cancelada. ${result.policy_explanation} Reembolso: ${formatMoney(result.refund_cents)}.`,
          status: 200,
        }),
      );
    } catch (caught) {
      setError(caught as ApiError);
    }
  };

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
          <Text style={[uiStyles.display, styles.title]} accessibilityRole="header">
            Mis reservas
          </Text>
          <Text style={styles.subtitle}>Todo lo que ya apartaste, en un solo lugar.</Text>

          <View style={styles.segment}>
            {(
              [
                ['upcoming', 'Próximas'],
                ['past', 'Pasadas'],
                ['cancelled', 'Canceladas'],
              ] as Array<[Tab, string]>
            ).map(([value, label]) => (
              <View
                key={value}
                onStartShouldSetResponder={() => true}
                onResponderRelease={() => setTab(value)}
                accessible
                accessibilityRole="tab"
                accessibilityState={{ selected: tab === value }}
                style={[styles.segmentItem, tab === value && styles.segmentItemActive]}
              >
                <Text style={[styles.segmentText, tab === value && styles.segmentTextActive]}>{label}</Text>
              </View>
            ))}
          </View>

          {error ? (
            <View style={{ marginBottom: space.md }}>
              <Notice
                tone={error.code === 'REFUND_ISSUED' ? 'info' : 'danger'}
                title={error.code === 'REFUND_ISSUED' ? 'Listo' : 'No se pudo cancelar'}
                body={error.message}
              />
            </View>
          ) : null}

          {bookings.isLoading ? (
            <ActivityIndicator color={color.sea} />
          ) : !next ? (
            <EmptyState
              title={tab === 'upcoming' ? 'No tienes eventos próximos' : 'Nada por aquí'}
              body={
                tab === 'upcoming'
                  ? 'Cuando apartes una fecha aparecerá aquí con su folio y la siguiente acción.'
                  : 'Aquí verás el historial cuando tengas eventos completados o cancelados.'
              }
              action={<Button label="Buscar un espacio" variant="quiet" onPress={() => router.replace('/')} />}
            />
          ) : (
            <Reveal index={0}>
              <NextPass booking={next} onCancel={() => void handleCancel(next)} cancelling={cancel.isPending} />
            </Reveal>
          )}

          {history.length ? (
            <>
              <SectionTitle title="Historial" />
              {history.map((booking, index) => (
                <Reveal key={booking.id} index={index + 1}>
                  <HistoryRow booking={booking} />
                </Reveal>
              ))}
            </>
          ) : null}

          <PolicyNote text="¿Necesitas cambiar la fecha? Habla con el anfitrión por la app: reprogramar conserva el precio original del apartado." />
        </View>
      </ScrollView>
    </View>
  );
}

function NextPass({
  booking,
  onCancel,
  cancelling,
}: {
  booking: BookingSummary;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const days = differenceInCalendarDays(parseISO(booking.start_at), new Date());
  const status = STATUS_LABEL[booking.state] ?? { text: booking.state, tone: 'done' as const };

  return (
    <View style={styles.pass}>
      <View style={styles.passStrip}>
        <Text style={styles.passStripText}>
          {days > 1 ? `FALTAN ${days} DÍAS` : days === 1 ? 'ES MAÑANA' : days === 0 ? 'ES HOY' : 'EN CURSO'}
        </Text>
        <Text style={styles.passStripText}>{booking.folio}</Text>
      </View>
      <View style={{ padding: space.md }}>
        <View style={{ flexDirection: 'row', gap: space.sm, alignItems: 'center' }}>
          <MediaFrame uri={booking.venue_cover_url} height={62} accessibilityLabel={`Foto de ${booking.venue_name ?? 'el espacio'}`} />
          <View style={{ flex: 1 }}>
            <Text style={styles.passTitle}>{booking.venue_name ?? 'Espacio'}</Text>
            <Text style={styles.passMeta}>
              {format(parseISO(booking.start_at), "EEE d 'de' MMM", { locale: es })} ·{' '}
              {format(parseISO(booking.start_at), 'HH:mm')} → {format(parseISO(booking.end_at), 'HH:mm')}
            </Text>
            <Text style={styles.passMeta}>
              {booking.guests} invitados ·{' '}
              {booking.balance_cents > 0
                ? `saldo ${formatMoney(booking.balance_cents, booking.currency)}`
                : 'pagado por completo'}
            </Text>
          </View>
          <View style={[styles.status, status.tone === 'ok' && styles.statusOk, status.tone === 'danger' && styles.statusDanger]}>
            <Text style={styles.statusText}>{status.text}</Text>
          </View>
        </View>

        <View style={{ flexDirection: 'row', gap: space.xs, marginTop: space.sm }}>
          {booking.can_pay_balance ? (
            <Button label="Pagar saldo" fullWidth={false} onPress={() => router.push(`/bookings`)} />
          ) : null}
          {booking.can_review ? (
            <Button label="Escribir reseña" fullWidth={false} onPress={() => router.push(`/bookings`)} />
          ) : null}
          {booking.can_cancel ? (
            <Button label="Cancelar" variant="quiet" fullWidth={false} loading={cancelling} onPress={onCancel} />
          ) : null}
        </View>
      </View>
    </View>
  );
}

function HistoryRow({ booking }: { booking: BookingSummary }) {
  const status = STATUS_LABEL[booking.state] ?? { text: booking.state, tone: 'done' as const };
  return (
    <View style={styles.historyRow}>
      <MediaFrame uri={booking.venue_cover_url} height={46} accessibilityLabel={`Foto de ${booking.venue_name ?? 'el espacio'}`} />
      <View style={{ flex: 1 }}>
        <Text style={styles.historyTitle}>{booking.venue_name ?? 'Espacio'}</Text>
        <Text style={styles.passMeta}>
          {format(parseISO(booking.start_at), "EEE d MMM yyyy", { locale: es })} · {booking.guests} invitados
        </Text>
      </View>
      <View style={[styles.status, status.tone === 'ok' && styles.statusOk, status.tone === 'danger' && styles.statusDanger]}>
        <Text style={styles.statusText}>{status.text}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  title: { fontSize: fontSize.headline, letterSpacing: -0.6 },
  subtitle: { fontFamily: type.body, fontSize: fontSize.small, color: color.inkSoft, marginTop: space.xxs, marginBottom: space.md },
  segment: { flexDirection: 'row', backgroundColor: color.bone, borderRadius: radius.control, padding: 3, marginBottom: space.md },
  segmentItem: { flex: 1, alignItems: 'center', paddingVertical: space.xs, borderRadius: radius.media },
  segmentItemActive: { backgroundColor: color.white },
  segmentText: { fontFamily: type.bodyMedium, fontSize: fontSize.caption, color: color.inkSoft },
  segmentTextActive: { fontFamily: type.bodySemi, color: color.ink },
  pass: { borderWidth: 1, borderColor: color.ink, borderRadius: radius.control, overflow: 'hidden', backgroundColor: color.white, marginBottom: space.lg },
  passStrip: { backgroundColor: color.sea, paddingHorizontal: space.sm, paddingVertical: space.xs, flexDirection: 'row', justifyContent: 'space-between' },
  passStripText: { fontFamily: type.bodySemi, fontSize: fontSize.caption, color: color.white, letterSpacing: 0.4 },
  passTitle: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  passMeta: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, lineHeight: fontSize.caption * lineHeight.body },
  historyRow: { flexDirection: 'row', alignItems: 'center', gap: space.sm, paddingVertical: space.xs, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: color.line },
  historyTitle: { fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink },
  status: { paddingHorizontal: space.xs, paddingVertical: 4, borderRadius: radius.media, backgroundColor: color.bone },
  statusOk: { backgroundColor: color.seaWash },
  statusDanger: { backgroundColor: color.dangerBg },
  statusText: { fontFamily: type.bodySemi, fontSize: 10, color: color.ink, letterSpacing: 0.4 },
});
