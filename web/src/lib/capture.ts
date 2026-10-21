/**
 * Modo captura (`#/?captura=1`).
 *
 * Existe por una razón concreta: las capturas automáticas (headless, CI, documentación) cortan la
 * animación de entrada y no alcanzan a cargar las imágenes en diferido, así que salen pantallas a
 * medio pintar y no sirven para revisar diseño. Con este modo, la página se dibuja en su estado
 * final de inmediato: sin transiciones y con todas las imágenes en carga inmediata.
 *
 * No cambia nada para un usuario normal: sólo se activa si la URL trae el parámetro.
 */

export function isCaptureMode(): boolean {
  if (typeof window === 'undefined') return false;
  const source = `${window.location.search}${window.location.hash}`;
  return /[?&]captura=1\b/.test(source);
}

export function applyCaptureMode(): void {
  if (!isCaptureMode()) return;
  document.documentElement.dataset.capture = 'true';
}
