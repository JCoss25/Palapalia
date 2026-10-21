/**
 * Pantalla 1 · Bienvenida.
 *
 * Una idea, una acción. La fecha va primero: el botón principal no dice «Comenzar» sino
 * «Buscar mi fecha». La foto es el espacio (no decoración) y el scrim sólo protege el texto.
 */

import React from 'react';
import { ImageBackground, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Button, Reveal, useReducedMotion } from '../src/components/ui';
import { color, fontSize, lineHeight, radius, space, type } from '../src/theme/tokens';

const HERO_URI = 'https://cdn.quinta.mx/reference/palapa-xcambo-noche.jpg';

export default function Onboarding() {
  const insets = useSafeAreaInsets();
  const reduced = useReducedMotion();

  return (
    <View style={styles.root}>
      <ImageBackground
        source={{ uri: HERO_URI }}
        style={StyleSheet.absoluteFill}
        resizeMode="cover"
        accessible
        accessibilityLabel="Palapas iluminadas junto al mar al anochecer"
      />
      {/* Scrim: único degradado permitido, existe para que el texto sea legible */}
      <View style={styles.scrim} />

      <View style={[styles.brand, { top: insets.top + space.sm }]}>
        <Text style={styles.brandMark}>Quinta</Text>
      </View>

      <View style={[styles.content, { paddingBottom: insets.bottom + space.xl }]}>
        <Reveal index={0}>
          <View style={styles.pager}>
            <View style={[styles.pagerItem, styles.pagerItemActive]} />
            <View style={styles.pagerItem} />
            <View style={styles.pagerItem} />
          </View>
        </Reveal>

        <Reveal index={1}>
          <Text style={styles.headline} accessibilityRole="header">
            La fecha{'\n'}va primero.
          </Text>
        </Reveal>

        <Reveal index={2}>
          <Text style={styles.body}>
            Palapas, quintas y salones con disponibilidad real. Aparta tu día, congela el precio y paga sin
            llamadas.
          </Text>
        </Reveal>

        <Reveal index={3}>
          <Button
            label="Buscar mi fecha"
            testID="onboarding-cta"
            accessibilityHint="Abre el buscador de espacios con el calendario abierto"
            onPress={() => router.replace('/')}
          />
          <View style={{ height: space.sm }} />
          <Button label="Ya tengo cuenta" variant="quiet" onPress={() => router.replace('/')} />
        </Reveal>

        {reduced ? null : (
          <Text style={styles.motionNote}>
            Animaciones reducidas activas: el contenido aparece sin desplazamiento.
          </Text>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#0B1713' },
  scrim: { ...StyleSheet.absoluteFillObject, backgroundColor: 'rgba(7,20,16,0.62)' },
  brand: { position: 'absolute', left: space.lg, flexDirection: 'row', alignItems: 'center' },
  brandMark: { fontFamily: type.display, fontSize: fontSize.title, color: color.bone, letterSpacing: -0.3 },
  content: { flex: 1, justifyContent: 'flex-end', paddingHorizontal: space.lg },
  pager: { flexDirection: 'row', gap: 6, marginBottom: space.lg },
  pagerItem: { width: 22, height: 3, borderRadius: radius.pill, backgroundColor: 'rgba(247,244,234,0.32)' },
  pagerItemActive: { width: 34, backgroundColor: color.bone },
  headline: {
    fontFamily: type.display,
    fontSize: fontSize.hero,
    lineHeight: fontSize.hero * 0.98,
    color: '#F7F4EA',
    letterSpacing: -1,
    marginBottom: space.md,
  },
  body: {
    fontFamily: type.body,
    fontSize: fontSize.body,
    lineHeight: fontSize.body * lineHeight.body,
    color: 'rgba(247,244,234,0.86)',
    maxWidth: 320,
    marginBottom: space.xl,
  },
  motionNote: {
    fontFamily: type.body,
    fontSize: fontSize.caption,
    color: 'rgba(247,244,234,0.55)',
    textAlign: 'center',
    marginTop: space.sm,
  },
});
