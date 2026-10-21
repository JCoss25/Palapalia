# 04 · Sistema de diseño (taste-skill)

## 4.1 Design read y diales

> **Design read:** «un cuaderno de apartados junto al mar» — papel hueso, tinta pino y un solo
> verde agua para la acción. La fecha manda: la primera decisión de cada pantalla es el día.

| Dial | Valor | Por qué |
|---|---|---|
| **VARIANCE** | 7 | Hay tres módulos distintos en el listado (tarjeta grande, fila compacta, enlace al listado) en vez de repetir la misma tarjeta. La pantalla de apartado tiene una composición única: el recibo con perforación. |
| **MOTION** | 4 | El movimiento informa (entrada escalonada por sección, respuesta al toque). Nada decorativo, nada que oculte contenido. Todo respeta reduce-motion. |
| **DENSITY** | 4 | Aire generoso, pero sin desperdiciar: el panel del anfitrión es más denso que el flujo de reserva porque su usuario lo usa a diario. |

Valores de plataforma (imagegen-frontend-mobile): `PLATFORM_MODE = iOS-neutral premium`,
`TEXTURE_STRENGTH = 3` (textura de papel sólo como tono de fondo, nunca ruido),
`COLOR_PALETTE_DISCIPLINE = 10`, `NON_GENERICITY = 10`, `TEXT_READABILITY_PRIORITY = 10`.

## 4.2 Idea y objeto recurrente

**On this app, el recibo de apartado (folio + reloj + desglose) se vuelve el objeto central de
todo el producto.** No es decoración: es el `BookingHold` real. Aparece en el detalle (CTA con la
fecha), en el checkout (recibo con perforación y cuenta regresiva) y en Mis reservas (pase con
folio y días restantes). Es lo único que este producto tiene y un clon genérico no.

La "cosa viva" de la app: **el reloj del apartado**. Se calcula contra `expires_at` del servidor,
se detiene en 00:00 y entonces la pantalla ofrece volver a apartar (nunca deja al usuario atorado).

## 4.3 Tokens (`mobile/src/theme/tokens.ts`)

| Rol | Valor | Uso |
|---|---|---|
| `bone` | `#EFEBDF` | Fondo de secciones alternas y de segmentos |
| `paper` | `#FBFAF6` | Superficie base de la app |
| `ink` | `#15201B` | Texto en cuerpo, titulares, chips activos |
| `inkSoft` | `#5D6660` | Notas secundarias (nunca texto corrido) |
| `sea` | `#0E5F52` | **Único acento**: acción principal, estados disponibles, éxito |
| `seaWash` | `#E4EDE8` | Fondo de avisos informativos |
| `sand` | `#E3DCCB` | Placeholder de imagen, deshabilitado |
| `amber` | `#B4771F` | Sólo estrellas de calificación (dato, no adorno) |
| `warningBg/Ink` | `#F6EDDC` / `#6A5637` | Bloqueos de verificación |
| `danger/dangerBg` | `#97402E` / `#F4E7E3` | Errores y estados cancelados |

Tipografía: **Fraunces** (display, sólo titulares cortos) + **Inter** (UI). Dos familias, el límite.
Escala de 7 pasos (`caption 11` → `hero 46`); el cuerpo nunca baja de 13 y el texto corrido se queda
en 15 con `lineHeight 1.5`.

Radios decididos una vez: `2` media, `4` controles, `16` hoja, `999` sólo chips. Botones cuadrados
con esquina de 4 px (no "soft radius"). Ningún gradiente salvo el scrim sobre foto. Ninguna sombra
salvo lo que de verdad flota (`shadow.floating`).

## 4.4 Reglas de la casa aplicadas (taste-skill)

| Regla | Cumplimiento |
|---|---|
| Titulares de dos líneas o menos | «La fecha va primero.» · «¿Qué vas a celebrar?» · «Octubre va lleno.» |
| Sin eyebrows ni badges sobre el titular | El kicker de categoría va *debajo* del titular de venue, como dato |
| Sin numeración 01/02/03 | La posición indica el orden; los pasos reales usan `<ol>` semántico en web |
| Sin reglas de 1 px decorativas | Separación por paso de tono (`ToneStep`) o espacio |
| Sin sombras en reposo | Sólo el FAB y la hoja |
| Sin paleta de asistente (crema+terracota, violeta) | Verde agua + hueso + pino |
| Sin glass, sin emoji decorativo, sin blobs | Cero |
| Máximo dos tipografías | Fraunces + Inter |
| Imágenes reales | 5 fotos con licencia en el board de referencia, acreditadas |
| Accesibilidad | `accessibilityLabel` en cada tarjeta con datos útiles, roles y estados; mínimo táctil 44 pt |

## 4.5 Motion (MOTION 4)

| Pieza | Implementación | Reduced-motion |
|---|---|---|
| Entrada de sección | `Reveal` con `withDelay(index * 70ms)` + `translateY 16 → 0` | Se muestra con opacidad 1 sin desplazamiento |
| Respuesta al toque | `PressableScale` 0.98 en `onPressIn` | Sin escala; el color de estado sigue informando |
| Reloj del apartado | Actualización por segundo con `fontVariant: tabular-nums` (sin animación) | Igual (es información, no adorno) |
| Transiciones de navegación | `slide_from_right` de expo-router | El sistema las respeta |

`useReducedMotion()` escucha `AccessibilityInfo.reduceMotionChanged`. **Ningún contenido depende de
la animación para ser visible**: si el JS falla, el contenido sigue ahí.

## 4.6 Pantallas de referencia

Board: [`docs/design/reference-screens.png`](design/reference-screens.png) ·
fuente editable: [`docs/design/reference-screens.html`](design/reference-screens.html).
Detalle de cada pantalla en [07-pantallas-referencia.md](07-pantallas-referencia.md).

### Créditos de imagen del board (referencia, no del producto)

| Imagen | Autor | Licencia | Fuente |
|---|---|---|---|
| Palm Leaves Palapa Beach Umbrella Eagle Beach Noord Aruba | EgorovaSvetlana | CC BY-SA 4.0 | Wikimedia Commons |
| Palapa Beach Umbrella Eagle Beach Noord Aruba | EgorovaSvetlana | CC BY-SA 4.0 | Wikimedia Commons |
| Night Palapa Beach Umbrellas Eagle Beach Noord Aruba | EgorovaSvetlana | CC BY-SA 4.0 | Wikimedia Commons |
| DZ6 0943 A brightly lit mobile bar… | PattayaPatrol | CC BY-SA 4.0 | Wikimedia Commons |
| Riversdale Estate garden urns and pathway… | Riversdale Estate | CC BY 2.0 | Wikimedia Commons |
| View from an infinity pool… | Jeffrey Paul | CC0 | WordPress Photo Directory |

En producción se sustituyen por la fotografía real de cada venue (subida por el proveedor y
moderada antes de publicarse).

## 4.7 Contenido de muestra a reemplazar antes de lanzar

| Elemento | Dónde | Nota |
|---|---|---|
| «Palapa Xcambó», «Quinta Alcántara», «Salón Casa Nube», «Quinta Los Arcos», «Palapa El Faro» | Board y textos | Nombres de ejemplo |
| Precios ($8,400 / $22,000 / $14,500) | Board | Cifras plausibles, no cotizadas |
| «Familia Pech», «M. y J. Uc», «R. Coss» | Panel del anfitrión | Nombres de ejemplo |
| URL `https://cdn.quinta.mx/reference/...` | `app/onboarding.tsx` | Sustituir por media real |
