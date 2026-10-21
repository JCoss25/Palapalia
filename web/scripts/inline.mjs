/**
 * Incrusta JS y CSS en el HTML del build de un solo archivo.
 *
 * Resultado: un `index.html` autocontenido (salvo las fotos de `./images`) que se abre con doble
 * clic, sin servidor, sin puerto y sin red. Es la vía para revisar el diseño cuando algo del
 * entorno local (proxy, puerto ocupado) impide verlo por HTTP.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const dir = join(process.cwd(), 'dist-single');
const htmlPath = join(dir, 'index.html');

if (!existsSync(htmlPath)) {
  console.error('No existe dist-single/index.html: corre primero `npm run build:single`.');
  process.exit(1);
}

let html = readFileSync(htmlPath, 'utf8');

// <script type="module" crossorigin src="./app.js"></script> → <script>…</script>
const scriptMatch = html.match(/<script[^>]*src="\.\/([^"]+\.js)"[^>]*><\/script>/);
if (!scriptMatch) {
  console.error('No encontré el script del bundle en index.html.');
  process.exit(1);
}
const js = readFileSync(join(dir, scriptMatch[1]), 'utf8');
// Dos detalles que rompen el archivo si se ignoran:
// 1. Reemplazo con función: pasar el JS como cadena hace que `.replace` interprete `$&`, `$'` y
//    `$$` como patrones de sustitución y corrompa el bundle (React minificado los contiene).
// 2. Un `<script>` clásico (no `type="module"`) en el `<head>` se ejecuta antes de que exista
//    `<div id="root">`. Se retira de su posición y se inyecta al final del `<body>`.
html = html.replace(scriptMatch[0], () => '');
html = html.replace('</body>', () => `<script>\n${js}\n</script>\n  </body>`);

// <link rel="stylesheet" href="./style.css"> → <style>…</style>
const cssMatch = html.match(/<link[^>]*href="\.\/([^"]+\.css)"[^>]*>/);
if (cssMatch) {
  const css = readFileSync(join(dir, cssMatch[1]), 'utf8');
  html = html.replace(cssMatch[0], () => `<style>\n${css}\n</style>`);
}

// Las fuentes de Google no se pueden cargar sin red: se dejan con fallback local declarado.
html = html.replace(
  /<link rel="preconnect"[^>]*>/g,
  '',
);

writeFileSync(htmlPath, html, 'utf8');

const kb = (Buffer.byteLength(html, 'utf8') / 1024).toFixed(0);
console.log(`index.html autocontenido · ${kb} KB · abrir con doble clic en dist-single/index.html`);
