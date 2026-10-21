/**
 * Pantalla 4 · Apartado (checkout).
 *
 * El recibo es el objeto central: folio, fecha, desglose y reloj real. El depósito de garantía
 * se muestra separado del total. El reloj se detiene en 00:00 y entonces la pantalla ofrece
 * volver a apartar en vez de dejar al usuario atorado.
 */

import React, { useEffect, useState } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { router, useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQuery } from '@tanstack/react-query';

import { http } from '../../src/api/client';
import { ApiError } from '../../src/api/client';
import { useConfirmBooking, useCreatePaymentIntent, useExtendHold } from '../../src/api/hooks';
import { formatMoney, type Hold, type Quote } from '../../src/api/types';
import { HoldTicket, PolicyNote } from '../../src/components/cards';
import { Button, Field, Notice, SectionTitle, styles as uiStyles } from '../../src/components/ui';
import { color, fontSize, space, type } from '../../src/theme/tokens';

type Method = 'card' | 'spei' | 'cash_at_venue';

export default function Checkout() {
  const { holdId } = useLocalSearchParams<{ holdId: string }>();
  const insets = useSafeAreaInsets();
  const [method, setMethod] = useState<Method>('card');
  const [wantsInvoice, setWantsInvoice] = useState(false);
  const [rfc, setRfc] = useState('');
  const [error, setError] = useState<ApiError | null>(null);

  const holdQuery = useQuery({
    queryKey: ['hold', holdId],
    queryFn: async () => {
      const { data } = await http.get(`/bookings/holds/${holdId}`);
      return data as Hold;
    },
  });

  const quoteQuery = useQuery({
    queryKey: ['hold-quote', holdId],
    queryFn: async () => {
      const { data } = await http.get(`/venues/${holdQuery.data!.venue_id}/quote`, {
        params: {
          start: holdQuery.data!.start_at,
          end: holdQuery.data!.end_at,
          guests: holdQuery.data!.guests,
        },
      });
      return data as Quote;
    },
    enabled: Boolean(holdQuery.data?.venue_id),
  });

  const confirm = useConfirmBooking();
  const intent = useCreatePaymentIntent();
  const extend = useExtendHold();

  const [expired, setExpired] = useState(false);
  useEffect(() => {
    if (!holdQuery.data) return;
    const remaining = (new Date(holdQuery.data.expires_at).getTime() - Date.now()) / 1000;
    setExpired(remaining <= 0);
    const timer = setInterval(() => {
      setExpired((new Date(holdQuery.data!.expires_at).getTime() - Date.now()) / 1000 <= 0);
    }, 1000);
    return () => clearInterval(timer);
  }, [holdQuery.data]);

  const hold = holdQuery.data;

  const handlePay = async () => {
    if (!hold) return;
    setError(null);
    try {
      const booking = await confirm.mutateAsync({
        hold_id: hold.id,
        payment_method: method,
        invoice_requested: wantsInvoice,
        rfc: wantsInvoice ? rfc : undefined,
      });

      if (method === 'card') {
        const payment = await intent.mutateAsync({ booking_id: booking.id, method: 'card' });
        if (payment.requires_action) {
          // El banco pide 3DS: se informa y se sigue el estado por notificación/webhook
          setError(
            new ApiError({
              code: 'PAYMENT_REQUIRES_ACTION',
              message: 'Tu banco pide confirmar la compra. Completa la verificación y te avisamos al confirmar.',
              status: 402,
            }),
          );
          return;
        }
      }
      router.replace('/bookings');
    } catch (caught) {
      const apiError = caught as ApiError;
      setError(apiError);
      if (apiError.code === 'HOLD_EXPIRED') setExpired(true);
    }
  };

  if (holdQuery.isLoading || !hold) {
    return (
      <View style={[uiStyles.screen, uiStyles.content, { paddingTop: insets.top + space.xl }]}>
        <Text style={{ fontFamily: type.body, color: color.inkSoft }}>Preparando tu apartado…</Text>
      </View>
    );
  }

  return (
    <View style={uiStyles.screen}>
      <ScrollView contentContainerStyle={{ paddingBottom: 140 + insets.bottom }} showsVerticalScrollIndicator={false}>
        <View style={[uiStyles.content, { paddingTop: insets.top + space.sm }]}>
          <View style={styles.nav}>
            <Text
              onPress={() => router.back()}
              accessibilityRole="button"
              accessibilityLabel="Volver"
              style={styles.navIcon}
            >
              ←
            </Text>
            <Text style={styles.navTitle}>Apartar</Text>
            <Text
              onPress={() => router.replace('/')}
              accessibilityRole="button"
              accessibilityLabel="Cerrar y volver al buscador"
              style={styles.navIcon}
            >
              ✕
            </Text>
          </View>

          {expired ? (
            <Notice
              tone="warning"
              title="Tu apartado venció"
              body="El horario se liberó. Si sigues interesado, aparta de nuevo: toma unos segundos."
              action={
                <Button
                  label="Volver a apartar"
                  variant="quiet"
                  onPress={() => router.replace(`/venue/${hold.venue_id}`)}
                />
              }
            />
          ) : null}

          <HoldTicket hold={hold} quote={quoteQuery.data} />

          {quoteQuery.data?.warnings?.length ? (
            <View style={{ marginTop: space.sm }}>
              {quoteQuery.data.warnings.map((warning) => (
                <Text key={warning} style={styles.warning}>
                  · {warning}
                </Text>
              ))}
            </View>
          ) : null}

          <PolicyNote
            text="Cancelación escalonada: 100% hasta 30 días antes, 50% entre 7 y 30 días. Después de 7 días no hay reembolso. El depósito de garantía se libera 48 h después del evento si no hay daños."
          />

          <SectionTitle title="Pago" />
          <MethodRow
            label="Tarjeta"
            detail="Cobro seguro con Stripe. Puedes guardar la tarjeta para el saldo."
            selected={method === 'card'}
            onPress={() => setMethod('card')}
          />
          <MethodRow
            label="Transferencia SPEI"
            detail="Recibes CLABE al confirmar. El apartado se mantiene hasta que se acredita."
            selected={method === 'spei'}
            onPress={() => setMethod('spei')}
          />
          <MethodRow
            label="Pago directo al anfitrión"
            detail="Sin comisión de servicio, pero sin protección de pago de Quinta."
            selected={method === 'cash_at_venue'}
            onPress={() => setMethod('cash_at_venue')}
          />

          <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: space.md, gap: space.xs }}>
            <Text
              onPress={() => setWantsInvoice((value) => !value)}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: wantsInvoice }}
              style={{ fontFamily: type.body, fontSize: fontSize.body, color: color.ink }}
            >
              {wantsInvoice ? '☑' : '☐'} Necesito factura (CFDI)
            </Text>
          </View>

          {wantsInvoice ? (
            <View style={{ marginTop: space.sm }}>
              <Field
                label="RFC"
                value={rfc}
                onChangeText={setRfc}
                placeholder="XAXX010101000"
                helper="Se solicita al PAC al confirmar el pago. Puedes corregirlo después."
              />
            </View>
          ) : null}

          {error ? (
            <View style={{ marginTop: space.md }}>
              <Notice
                tone={error.code === 'VENUE_NOT_AVAILABLE' || error.code === 'HOLD_EXPIRED' ? 'warning' : 'danger'}
                title={
                  error.code === 'CARD_DECLINED'
                    ? 'El banco rechazó la tarjeta'
                    : error.code === 'PAYMENT_DUPLICATED'
                      ? 'Ese pago ya estaba registrado'
                      : 'No pudimos completar el pago'
                }
                body={error.message}
                action={
                  error.code === 'HOLD_EXPIRED' ? (
                    <Button label="Apartar de nuevo" variant="quiet" onPress={() => router.replace('/')} />
                  ) : (
                    <Button label="Intentar con otro método" variant="quiet" onPress={() => setMethod('spei')} />
                  )
                }
              />
            </View>
          ) : null}
        </View>
      </ScrollView>

      <View style={[styles.dock, { paddingBottom: insets.bottom + space.sm }]}>
        <Button
          label={
            expired
              ? 'El apartado venció'
              : `Pagar ${formatMoney(quoteQuery.data?.deposit_cents ?? hold.deposit_cents, hold.currency)} y apartar`
          }
          disabled={expired}
          loading={confirm.isPending || intent.isPending}
          testID="checkout-pay"
          onPress={() => void handlePay()}
        />
        <Text style={styles.fine}>
          Al pagar aceptas el contrato de apartado y la política de cancelación.
        </Text>
      </View>
    </View>
  );
}

function MethodRow({
  label,
  detail,
  selected,
  onPress,
}: {
  label: string;
  detail: string;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <View
      onStartShouldSetResponder={() => true}
      onResponderRelease={onPress}
      accessible
      accessibilityRole="radio"
      accessibilityState={{ selected }}
      accessibilityLabel={`${label}. ${detail}`}
      style={[styles.method, selected && styles.methodSelected]}
    >
      <View style={{ flex: 1 }}>
        <Text style={styles.methodLabel}>{label}</Text>
        <Text style={styles.methodDetail}>{detail}</Text>
      </View>
      <View style={[styles.radio, selected && styles.radioSelected]} />
    </View>
  );
}

const styles = StyleSheet.create({
  nav: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: space.md },
  navTitle: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  navIcon: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink, paddingHorizontal: space.xs },
  warning: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, lineHeight: 18 },
  method: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: space.sm,
    borderWidth: 1,
    borderColor: color.line,
    borderRadius: 3,
    padding: space.sm,
    marginBottom: space.xs,
    backgroundColor: color.white,
  },
  methodSelected: { borderColor: color.sea },
  methodLabel: { fontFamily: type.bodyMedium, fontSize: fontSize.small, color: color.ink },
  methodDetail: { fontFamily: type.body, fontSize: fontSize.caption, color: color.inkSoft, marginTop: 2 },
  radio: {
    width: 17,
    height: 17,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: color.lineStrong,
  },
  radioSelected: { borderWidth: 5, borderColor: color.sea },
  dock: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    paddingHorizontal: space.lg,
    paddingTop: space.sm,
    backgroundColor: color.paper,
    borderTopWidth: 1,
    borderTopColor: color.line,
  },
  fine: {
    fontFamily: type.body,
    fontSize: 10,
    color: color.inkSoft,
    textAlign: 'center',
    marginTop: space.xs,
    lineHeight: 14,
  },
});
