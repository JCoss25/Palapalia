/**
 * Router mínimo por hash.
 *
 * Se evita una dependencia para que la app arranque sin configuración de servidor (un `#/ruta`
 * funciona en cualquier hosting estático) y para que los enlaces sean compartibles.
 */

import { useEffect, useState } from 'react';

export interface Route {
  path: string;
  segments: string[];
  query: URLSearchParams;
}

function parse(hash: string): Route {
  const raw = hash.replace(/^#/, '') || '/';
  const parts = raw.split('?');
  const pathname = parts[0] ?? '/';
  const search = parts[1] ?? '';
  const path = pathname === '' ? '/' : pathname;
  return {
    path,
    segments: path.split('/').filter(Boolean),
    query: new URLSearchParams(search),
  };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));

  useEffect(() => {
    const onChange = () => {
      setRoute(parse(window.location.hash));
      // Al cambiar de pantalla se vuelve al inicio: sin esto, el scroll queda a media página.
      window.scrollTo({ top: 0, behavior: 'auto' });
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  return route;
}

export function navigate(to: string): void {
  const next = to.startsWith('#') ? to : `#${to}`;
  if (window.location.hash === next) return;
  window.location.hash = next;
}

export function href(to: string): string {
  return to.startsWith('#') ? to : `#${to}`;
}

/** Intercepta clics en enlaces internos para no recargar la página. */
export function onLinkClick(
  event: React.MouseEvent<HTMLAnchorElement>,
  to: string,
): void {
  if (event.metaKey || event.ctrlKey || event.shiftKey) return;
  event.preventDefault();
  navigate(to);
}
