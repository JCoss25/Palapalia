/**
 * Primitivas de UI de Quinta.
 *
 * Todas respetan `AccessibilityInfo.isReduceMotionEnabled`: cuando está activo, el contenido
 * aparece sin desplazamiento (sólo opacidad, o de plano sin animación). Ningún elemento
 * esencial depende de la animación para ser visible.
 */

import React, { useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type PressableProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import Animated, {
  Easing,
  useAnimatedStyle,
  useSharedValue,
  withDelay,
  withTiming,
} from 'react-native-reanimated';

import { color, control, fontSize, hitSlop, lineHeight, motion, radius, space, type } from '../theme/tokens';

/** Hook de accesibilidad: se usa en todo componente que se mueva. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    let mounted = true;
    void AccessibilityInfo.isReduceMotionEnabled().then((value) => {
      if (mounted) setReduced(value);
    });
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', (value) => {
      if (mounted) setReduced(value);
    });
    return () => {
      mounted = false;
      subscription.remove();
    };
  }, []);
  return reduced;
}

/** Entrada escalonada por sección: sube 16 px y aparece. Sin movimiento si hay reduce-motion. */
export function Reveal({
  children,
  index = 0,
  style,
}: {
  children: React.ReactNode;
  index?: number;
  style?: ViewStyle;
}) {
  const reduced = useReducedMotion();
  const progress = useSharedValue(reduced ? 1 : 0);

  useEffect(() => {
    progress.value = reduced
      ? 1
      : withDelay(index * motion.stagger, withTiming(1, { duration: motion.base, easing: Easing.out(Easing.cubic) }));
  }, [index, progress, reduced]);

  const animatedStyle = useAnimatedStyle(() => ({
    opacity: progress.value,
    transform: [{ translateY: reduced ? 0 : (1 - progress.value) * motion.rise }],
  }));

  return <Animated.View style={[style, animatedStyle]}>{children}</Animated.View>;
}

/** Respuesta táctil: escala 0.98 al presionar. Nunca es la única señal de estado. */
export function PressableScale({ children, style, ...rest }: PressableProps & { style?: ViewStyle }) {
  const reduced = useReducedMotion();
  const scale = useSharedValue(1);
  const animatedStyle = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));
  return (
    <Pressable
      {...rest}
      hitSlop={hitSlop}
      onPressIn={() => {
        if (!reduced) scale.value = withTiming(0.98, { duration: motion.fast });
      }}
      onPressOut={() => {
        scale.value = withTiming(1, { duration: motion.fast });
      }}
    >
      <Animated.View style={[style, animatedStyle]}>{children}</Animated.View>
    </Pressable>
  );
}

type ButtonVariant = 'primary' | 'secondary' | 'quiet' | 'danger';

export function Button({
  label,
  onPress,
  variant = 'primary',
  loading = false,
  disabled = false,
  fullWidth = true,
  accessibilityHint,
  testID,
}: {
  label: string;
  onPress: () => void;
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  fullWidth?: boolean;
  accessibilityHint?: string;
  testID?: string;
}) {
  const isDisabled = disabled || loading;
  const palette: Record<ButtonVariant, { bg: string; fg: string; border?: string }> = {
    primary: { bg: color.sea, fg: color.white },
    secondary: { bg: color.bone, fg: color.ink },
    quiet: { bg: 'transparent', fg: color.ink, border: color.line },
    danger: { bg: color.dangerBg, fg: color.danger },
  };
  const tone = palette[variant];

  return (
    <PressableScale
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: isDisabled, busy: loading }}
      disabled={isDisabled}
      onPress={onPress}
      style={{
        minHeight: control.button,
        borderRadius: radius.control,
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: isDisabled ? color.sand : tone.bg,
        borderWidth: tone.border ? 1 : 0,
        borderColor: tone.border,
        width: fullWidth ? '100%' : undefined,
        paddingHorizontal: space.lg,
      }}
    >
      {loading ? (
        <ActivityIndicator color={tone.fg} />
      ) : (
        <Text
          style={{
            color: isDisabled ? color.inkSoft : tone.fg,
            fontFamily: type.bodySemi,
            fontSize: fontSize.body,
          }}
        >
          {label}
        </Text>
      )}
    </PressableScale>
  );
}

export function Chip({
  label,
  selected = false,
  onPress,
  icon,
}: {
  label: string;
  selected?: boolean;
  onPress?: () => void;
  icon?: React.ReactNode;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      onPress={onPress}
      hitSlop={hitSlop}
      style={{
        height: control.chip,
        paddingHorizontal: space.md,
        borderRadius: radius.pill,
        borderWidth: 1,
        borderColor: selected ? color.ink : color.line,
        backgroundColor: selected ? color.ink : 'transparent',
        flexDirection: 'row',
        alignItems: 'center',
        gap: space.xxs,
      }}
    >
      {icon}
      <Text
        style={{
          color: selected ? color.bone : color.ink,
          fontFamily: selected ? type.bodySemi : type.bodyMedium,
          fontSize: fontSize.small,
        }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function Field({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType = 'default',
  error,
  helper,
  secureTextEntry,
  testID,
}: {
  label: string;
  value: string;
  onChangeText: (value: string) => void;
  placeholder?: string;
  keyboardType?: 'default' | 'numeric' | 'email-address' | 'phone-pad';
  error?: string | null;
  helper?: string;
  secureTextEntry?: boolean;
  testID?: string;
}) {
  return (
    <View style={{ marginBottom: space.md }}>
      <Text style={{ fontSize: fontSize.small, color: color.inkSoft, marginBottom: space.xxs }}>{label}</Text>
      <TextInput
        testID={testID}
        accessibilityLabel={label}
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={color.inkFaint}
        keyboardType={keyboardType}
        secureTextEntry={secureTextEntry}
        style={{
          minHeight: control.input,
          borderWidth: 1,
          borderColor: error ? color.danger : color.line,
          borderRadius: radius.control,
          paddingHorizontal: space.sm,
          backgroundColor: color.white,
          color: color.ink,
          fontFamily: type.body,
          fontSize: fontSize.body,
        }}
      />
      {error ? (
        <Text style={{ color: color.danger, fontSize: fontSize.caption, marginTop: space.xxs }}>{error}</Text>
      ) : helper ? (
        <Text style={{ color: color.inkSoft, fontSize: fontSize.caption, marginTop: space.xxs }}>{helper}</Text>
      ) : null}
    </View>
  );
}

/** Separación por paso de tono, nunca por línea de 1 px. */
export function ToneStep({ children, tone = 'bone' }: { children: React.ReactNode; tone?: 'bone' | 'paper' | 'wash' }) {
  const backgrounds = { bone: color.bone, paper: color.paper, wash: color.seaWash };
  return (
    <View style={{ backgroundColor: backgrounds[tone], padding: space.md, borderRadius: radius.control }}>
      {children}
    </View>
  );
}

export function StarRating({ value, count }: { value: number | null; count?: number }) {
  if (value === null) return null;
  return (
    <View
      style={{ flexDirection: 'row', alignItems: 'center', gap: space.xxs }}
      accessibilityLabel={`Calificación ${value} de 5${count ? `, ${count} reseñas` : ''}`}
    >
      <Text style={{ color: color.amber, fontSize: fontSize.small }}>★</Text>
      <Text style={{ fontFamily: type.bodySemi, fontSize: fontSize.small, color: color.ink }}>
        {value.toFixed(1)}
      </Text>
      {count ? <Text style={{ color: color.inkSoft, fontSize: fontSize.small }}>({count})</Text> : null}
    </View>
  );
}

export function SectionTitle({
  title,
  action,
  onAction,
}: {
  title: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <View
      style={{
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'baseline',
        marginBottom: space.sm,
      }}
    >
      <Text style={styles.sectionTitle}>{title}</Text>
      {action ? (
        <Pressable onPress={onAction} hitSlop={hitSlop} accessibilityRole="button">
          <Text style={{ color: color.sea, fontFamily: type.bodySemi, fontSize: fontSize.small }}>{action}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

export function EmptyState({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <View style={{ paddingVertical: space.xxl, alignItems: 'flex-start', gap: space.sm }}>
      <Text style={{ fontFamily: type.display, fontSize: fontSize.headline, color: color.ink }}>{title}</Text>
      <Text style={{ fontFamily: type.body, fontSize: fontSize.body, color: color.inkSoft, lineHeight: fontSize.body * lineHeight.body }}>
        {body}
      </Text>
      {action}
    </View>
  );
}

/** Aviso de estado degradado o de error accionable (nunca un callejón sin salida). */
export function Notice({
  tone,
  title,
  body,
  action,
}: {
  tone: 'info' | 'warning' | 'danger';
  title: string;
  body: string;
  action?: React.ReactNode;
}) {
  const palette = {
    info: { bg: color.seaWash, fg: color.seaDeep },
    warning: { bg: color.warningBg, fg: color.warningInk },
    danger: { bg: color.dangerBg, fg: color.danger },
  }[tone];
  return (
    <View style={{ backgroundColor: palette.bg, borderRadius: radius.control, padding: space.sm, gap: space.xxs }}>
      <Text style={{ fontFamily: type.bodySemi, fontSize: fontSize.small, color: palette.fg }}>{title}</Text>
      <Text style={{ fontFamily: type.body, fontSize: fontSize.small, color: palette.fg, lineHeight: fontSize.small * lineHeight.body }}>
        {body}
      </Text>
      {action}
    </View>
  );
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: color.paper },
  content: { paddingHorizontal: space.lg },
  sectionTitle: { fontFamily: type.bodySemi, fontSize: fontSize.body, color: color.ink },
  display: { fontFamily: type.display, color: color.ink, letterSpacing: -0.5 },
} satisfies Record<string, ViewStyle | TextStyle>);
