/** Páginas de contenido y 404: texto plano, sin adornos. */

import { href, navigate, onLinkClick } from '../lib/router';

export function PolicyPage() {
  return (
    <main id="contenido" className="section-tight">
      <div className="wrap" style={{ maxWidth: '68ch' }}>
        <h1 className="display display-lg">Política de cancelación</h1>
        <div className="stack-lg" style={{ marginTop: 'var(--s-6)' }}>
          <p className="lead">
            La política es la misma para todos los espacios de Palapalia, y se te muestra antes de pagar con el
            cálculo hecho sobre tu fecha.
          </p>
          <div className="stack">
            <h2 className="display display-md">Antes del evento</h2>
            <ul className="stack" style={{ paddingLeft: 'var(--s-5)', gap: 'var(--s-2)' }}>
              <li>Cancelas con 30 días o más: reembolso total del anticipo.</li>
              <li>Cancelas entre 7 y 30 días antes: reembolso del 50% del anticipo.</li>
              <li>Cancelas a menos de 7 días: no hay reembolso del anticipo.</li>
            </ul>
          </div>
          <div className="stack">
            <h2 className="display display-md">Depósito de garantía</h2>
            <p className="body-soft measure">
              Se retiene aparte del total y se libera 48 horas después del evento, siempre que no haya daños
              documentados con fotos y notas firmadas. Si hay daños, se cobra sólo el monto acreditado y
              recibes el detalle.
            </p>
          </div>
          <div className="stack">
            <h2 className="display display-md">Si cancela el anfitrión</h2>
            <p className="body-soft measure">
              El reembolso es total, incluida la comisión de servicio, y el anfitrión recibe una penalización en
              su posición de búsqueda. Te ofrecemos espacios equivalentes para la misma fecha antes de
              devolverte el dinero, pero decides tú.
            </p>
          </div>
          <div className="stack">
            <h2 className="display display-md">Disputas</h2>
            <p className="body-soft measure">
              Puedes abrir una disputa hasta 7 días después del evento. El pago al anfitrión queda retenido
              mientras se revisa, y la resolución se documenta con evidencias de las dos partes.
            </p>
          </div>
          <button type="button" className="btn btn-quiet" onClick={() => navigate('/')}>
            Volver al buscador
          </button>
        </div>
      </div>
    </main>
  );
}

export function ApiPage() {
  return (
    <main id="contenido" className="section-tight">
      <div className="wrap" style={{ maxWidth: '68ch' }}>
        <h1 className="display display-lg">La API de Palapalia</h1>
        <div className="stack-lg" style={{ marginTop: 'var(--s-6)' }}>
          <p className="lead">
            Esta web es un cliente más: habla con el mismo backend FastAPI que la app móvil, con el mismo
            contrato de errores y las mismas reglas de dinero.
          </p>
          <div className="stack">
            <h2 className="display display-md">Documentación viva</h2>
            <p className="body-soft measure">
              Con el backend arriba, Swagger UI está en <code>http://127.0.0.1:8000/docs</code> y ReDoc en{' '}
              <code>/redoc</code>. El esquema OpenAPI 3.1 sale de los modelos Pydantic, así que no se
              desincroniza del código.
            </p>
          </div>
          <div className="stack">
            <h2 className="display display-md">Reglas que respeta este cliente</h2>
            <ul className="stack" style={{ paddingLeft: 'var(--s-5)', gap: 'var(--s-2)' }}>
              <li>Dinero en centavos enteros, nunca en decimales flotantes.</li>
              <li>
                <code>Idempotency-Key</code> en las escrituras críticas: apartar, pagar, reembolsar.
              </li>
              <li>
                Los errores se deciden por código (<code>VENUE_NOT_AVAILABLE</code>, <code>HOLD_EXPIRED</code>),
                no por el texto del mensaje.
              </li>
              <li>Fechas en UTC con la zona del espacio para mostrar la hora local correcta.</li>
            </ul>
          </div>
          <div className="stack">
            <h2 className="display display-md">Estado de esta demo</h2>
            <p className="body-soft measure">
              Si la API no responde en este puerto, la web funciona con datos de ejemplo pero conserva los
              cálculos reales de anticipo, comisión, IVA, depósito de garantía y política escalonada. Verás el
              aviso arriba cuando eso ocurra.
            </p>
          </div>
          <button type="button" className="btn btn-quiet" onClick={() => navigate('/')}>
            Volver al buscador
          </button>
        </div>
      </div>
    </main>
  );
}

export function NotFoundPage() {
  return (
    <main id="contenido" className="section">
      <div className="wrap" style={{ maxWidth: '60ch' }}>
        <h1 className="display display-lg">
          Esa página
          <br />
          no existe.
        </h1>
        <p className="body-soft measure" style={{ marginTop: 'var(--s-4)' }}>
          Puede que el enlace esté mal escrito o que el espacio ya no esté publicado. Desde el buscador llegas
          en dos clics.
        </p>
        <div className="row" style={{ marginTop: 'var(--s-5)' }}>
          <a className="btn" href={href('/')} onClick={(event) => onLinkClick(event, '/')}>
            Ir al buscador
          </a>
        </div>
      </div>
    </main>
  );
}
