/**
 * Estructura compartida: encabezado, pie, aviso de modo demostración y entrada por sección.
 *
 * Reglas aplicadas: navegación plana (marca + enlaces + una acción), sin chrome de barra;
 * el pie es un cierre diseñado —el nombre vuelve a tamaño grande— y no un mapa del sitio.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import { href, navigate, onLinkClick, useRoute } from '../lib/router';

export function Reveal({
  children,
  delay = 0,
  as: Tag = 'div',
  className = '',
}: {
  children: ReactNode;
  delay?: number;
  as?: 'div' | 'section' | 'article' | 'li';
  className?: string;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    // Si el navegador no soporta IntersectionObserver, el contenido se muestra igual.
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        });
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.05 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <Tag
      ref={ref as never}
      className={`reveal ${className}`.trim()}
      data-visible={visible ? 'true' : 'false'}
      style={{ ['--reveal-delay' as string]: `${delay}ms` }}
    >
      {children}
    </Tag>
  );
}

export function Header({ demo }: { demo: boolean }) {
  const route = useRoute();
  const isProvider = route.path.startsWith('/panel');

  return (
    <>
      <a className="skip-link" href="#contenido">
        Saltar al contenido
      </a>
      <header className="header">
        <div className="wrap header-inner">
          <a
            className="wordmark"
            href={href('/')}
            onClick={(event) => onLinkClick(event, '/')}
            aria-label="Quinta, inicio"
          >
            Quinta
          </a>
          <nav className="nav" aria-label="Navegación principal">
            <a href={href('/')} onClick={(event) => onLinkClick(event, '/')}>
              Explorar
            </a>
            <a href={href('/reservas')} onClick={(event) => onLinkClick(event, '/reservas')}>
              Mis reservas
            </a>
            <a href={href('/panel')} onClick={(event) => onLinkClick(event, '/panel')}>
              Soy anfitrión
            </a>
          </nav>
          <div className="row" style={{ gap: 'var(--s-2)' }}>
            <button
              type="button"
              className="btn btn-quiet btn-sm"
              onClick={() => navigate(isProvider ? '/' : '/panel')}
            >
              {isProvider ? 'Ver como cliente' : 'Panel del anfitrión'}
            </button>
          </div>
        </div>
      </header>
      {demo ? (
        <div className="tone-wash" role="status">
          <div className="wrap" style={{ paddingBlock: 'var(--s-2)', fontSize: '0.8125rem' }}>
            Modo demostración: la API no está conectada en este puerto, así que los espacios, precios y reservas
            son de ejemplo. Los cálculos de anticipo, comisión y política de cancelación sí son los reales.
          </div>
        </div>
      ) : null}
    </>
  );
}

export function Footer() {
  return (
    <footer className="footer">
      <div className="wrap">
        <p className="footer-name">Quinta</p>
        <div className="footer-grid" style={{ marginTop: 'var(--s-7)' }}>
          <div className="stack" style={{ gap: 'var(--s-3)' }}>
            <p className="body-soft" style={{ maxWidth: '38ch' }}>
              Palapas, quintas y salones para eventos, con disponibilidad real y precio congelado al apartar.
              Operamos en Yucatán, Querétaro, Jalisco, Oaxaca y Ciudad de México.
            </p>
          </div>
          <div className="stack" style={{ gap: 'var(--s-3)' }}>
            <p style={{ fontWeight: 600 }}>Para quien celebra</p>
            <a href={href('/')} onClick={(event) => onLinkClick(event, '/')}>
              Buscar por fecha
            </a>
            <a href={href('/reservas')} onClick={(event) => onLinkClick(event, '/reservas')}>
              Mis reservas
            </a>
            <a href={href('/politica')} onClick={(event) => onLinkClick(event, '/politica')}>
              Política de cancelación
            </a>
          </div>
          <div className="stack" style={{ gap: 'var(--s-3)' }}>
            <p style={{ fontWeight: 600 }}>Para anfitriones</p>
            <a href={href('/panel')} onClick={(event) => onLinkClick(event, '/panel')}>
              Panel y agenda
            </a>
            <a href={href('/panel')} onClick={(event) => onLinkClick(event, '/panel')}>
              Verificación y pagos
            </a>
            <a href={href('/api')} onClick={(event) => onLinkClick(event, '/api')}>
              Documentación de la API
            </a>
          </div>
        </div>
        <p className="caption" style={{ marginTop: 'var(--s-7)' }}>
          Los precios se muestran en pesos mexicanos e incluyen IVA sobre la comisión de servicio. El depósito de
          garantía es reembolsable.
        </p>
      </div>
    </footer>
  );
}

/** Encabezado de sección: título y, si aplica, una acción secundaria. */
export function SectionHead({
  title,
  note,
  action,
  onAction,
}: {
  title: string;
  note?: string;
  action?: string;
  onAction?: () => void;
}) {
  return (
    <div className="row-between" style={{ marginBottom: 'var(--s-4)', alignItems: 'baseline' }}>
      <div>
        <h2 className="display display-md">{title}</h2>
        {note ? <p className="caption" style={{ marginTop: 'var(--s-2)' }}>{note}</p> : null}
      </div>
      {action ? (
        <button type="button" className="btn btn-quiet btn-sm" onClick={onAction}>
          {action}
        </button>
      ) : null}
    </div>
  );
}

export function Stars({ rating, reviews }: { rating: number; reviews?: number }) {
  return (
    <span className="stars" aria-label={`Calificación ${rating} de 5${reviews ? `, ${reviews} reseñas` : ''}`}>
      ★ {rating.toFixed(1)}
      {reviews ? <span className="caption" style={{ marginLeft: 6 }}>({reviews})</span> : null}
    </span>
  );
}
