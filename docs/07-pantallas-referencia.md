# 07 · Pantallas de referencia móvil (imagegen-frontend-mobile)

Generadas **antes** del código de UI, con la skill `imagegen-frontend-mobile` (diales por defecto
ajustados: `DESIGN_VARIANCE 8`, `VISUAL_DENSITY 3`, `TEXT_READABILITY_PRIORITY 10`,
`CONSISTENCY_STRENGTH 10`, `MOCKUP_FRAME_DISCIPLINE 9`).

- Board: [`design/reference-screens.png`](design/reference-screens.png)
- Fuente editable (HTML + CSS + fotos): [`design/reference-screens.html`](design/reference-screens.html)

**Design bible fijada antes de generar (una sola para las 6 pantallas):** iOS-neutral premium ·
mockup iPhone con bisel limpio y margen exterior uniforme · paleta hueso/pino/verde agua ·
Fraunces + Inter · radio 2 media / 4 control / 999 chip · un solo acento (`sea`) ·
navegación por tab bar de 4 destinos · fotografía editorial con scrim inferior cuando hay texto
encima · sin sombras salvo el botón flotante · iconografía de trazo 1.7 coherente.

## 7.1 Pantalla 1 · Bienvenida (onboarding)

| Aspecto | Decisión |
|---|---|
| Composición | Foto a sangre completa con scrim inferior; texto abajo, marca arriba |
| Titular | «La fecha va primero.» (2 líneas, 46 px) |
| Acción | «Buscar mi fecha» (nombra la acción, no «Comenzar») |
| Por qué funciona | El producto no vende espacios, vende fechas: la primera frase es el modelo mental |
| Evita | Tres slides idénticos con icono; copy motivacional de relleno |

## 7.2 Pantalla 2 · Explorar (home + búsqueda)

| Aspecto | Decisión |
|---|---|
| Jerarquía | Fecha arriba (tira horizontal), luego tipo de celebración, luego resultados |
| Módulos | Tarjeta grande + sección «También libres el N» con filas compactas + enlace al listado |
| Dato clave por tarjeta | «Libre el 18 de oct» antes que el precio: responde la pregunta real |
| Transparencia | Etiqueta **Patrocinado** sobre la primera tarjeta cuando corresponde |
| Estado vacío | «Nada libre el 18 de octubre» + botón «Ver otra fecha» (nunca un callejón sin salida) |

## 7.3 Pantalla 3 · Detalle del espacio

| Aspecto | Decisión |
|---|---|
| Bloque principal | Calendario de disponibilidad (no una pestaña escondida) |
| Datos duros | Capacidad, precio por día y anticipo en una banda de tres hechos |
| CTA | «Apartar el 17 de oct»: nombra la fecha, no «Reservar» |
| Riesgo explicado | Buffer de limpieza y horas mínimas visibles antes de intentar |
| Estado no disponible | Motivo explícito (`blackout` vs. `booked`) y sugerencia de fechas contiguas |

## 7.4 Pantalla 4 · Apartado (checkout)

| Aspecto | Decisión |
|---|---|
| Objeto central | El recibo con folio, perforación, desglose y reloj de 14:52 |
| Dinero honesto | Depósito de garantía (reembolsable) separado del total |
| Congelamiento | «El precio queda congelado mientras el apartado esté activo» |
| Métodos | Tarjeta / SPEI / pago directo, cada uno con su contrapartida dicha en una línea |
| Reloj vencido | La pantalla cambia de estado y ofrece «Volver a apartar» (edge case A2) |

## 7.5 Pantalla 5 · Mis reservas

| Aspecto | Decisión |
|---|---|
| Próxima reserva | Se lee como un pase: banda verde con «FALTAN 12 DÍAS» y folio |
| Acción siguiente | Un solo botón principal («Pagar saldo»), el resto en jerarquía menor |
| Historial | Filas compactas con estado en pastilla: `COMPLETADA`, `RESEÑAR`, `REEMBOLSADA` |
| Ayuda contextual | «¿Cambio de fecha? Habla con el anfitrión antes del 10 de oct para conservar tu anticipo» |

## 7.6 Pantalla 6 · Panel del anfitrión

| Aspecto | Decisión |
|---|---|
| Bloqueo primero | «Falta tu constancia de situación fiscal» con acción y fecha límite |
| Números reales | Ocupación 78% con días marcados en verde, ingreso del mes, ticket promedio |
| Sin gráficas decorativas | Barras con valor accesible por día (L M M J V S D), no un gráfico genérico |
| Operación | Próximos eventos con folio, personas y estado del cobro (pagado/anticipo) |
| Acción flotante | Un solo FAB (agregar) — el único elemento con sombra |

## 7.7 Consistencia verificada

| Criterio | Estado |
|---|---|
| ¿Parece app, no web en un teléfono? | Sí: safe areas, tab bar, hojas, sin hero de landing |
| ¿Safe areas respetadas? | Sí en las 6 (status bar y home indicator visibles) |
| ¿Primera pantalla limpia? | Una idea, un CTA |
| ¿Texto legible? | Mínimo 10 px sólo en etiquetas de eje; cuerpo 13–15; titulares 26–46 |
| ¿Sin tells de IA móvil? | Sin degradados púrpura, sin glass, sin pills decorativos, sin charts falsos |
| ¿Mockup uniforme? | Mismo bisel, misma escala y mismo margen en las 6 |
| ¿Variación sin romper el sistema? | Foto-led (1), lista (2), galería (3), recibo (4), pase (5), datos (6) |
| ¿Imágenes con propósito? | 5 fotos reales con licencia, recortes consistentes 16:10 y 1:1 |
