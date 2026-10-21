/**
 * Composición de la aplicación: router por hash, encabezado con aviso de modo demostración y
 * selección de pantalla según la ruta.
 */

import { useEffect, useState } from 'react';

import { apiIsQuinta, demoListeners, usingDemoData } from './api/client';
import { Footer, Header } from './components/Layout';
import { BookingsPage } from './pages/Bookings';
import { CheckoutPage } from './pages/Checkout';
import { HomePage } from './pages/Home';
import { ProviderPage } from './pages/Provider';
import { ApiPage, NotFoundPage, PolicyPage } from './pages/Static';
import { VenueDetailPage } from './pages/VenueDetail';
import { useRoute } from './lib/router';

function useDemoMode(): boolean {
  const [demo, setDemo] = useState(usingDemoData);
  useEffect(() => {
    demoListeners.add(setDemo);
    // Comprobación única al arrancar: ¿la API detrás de /api es la de Quinta?
    void apiIsQuinta();
    return () => {
      demoListeners.delete(setDemo);
    };
  }, []);
  return demo;
}

export function App() {
  const route = useRoute();
  const demo = useDemoMode();

  const [first, second] = route.segments;

  let page: React.ReactNode;
  switch (first) {
    case undefined:
      page = <HomePage />;
      break;
    case 'espacio':
      page = second ? (
        <VenueDetailPage venueId={second} initialDate={route.query.get('fecha') ?? undefined} />
      ) : (
        <NotFoundPage />
      );
      break;
    case 'apartar':
      page = second ? (
        <CheckoutPage
          venueId={second}
          startIso={route.query.get('inicio') ?? undefined}
          endIso={route.query.get('fin') ?? undefined}
          guests={Number(route.query.get('invitados') ?? 80)}
        />
      ) : (
        <NotFoundPage />
      );
      break;
    case 'reservas':
      page = <BookingsPage />;
      break;
    case 'panel':
      page = <ProviderPage />;
      break;
    case 'politica':
      page = <PolicyPage />;
      break;
    case 'api':
      page = <ApiPage />;
      break;
    default:
      page = <NotFoundPage />;
  }

  return (
    <>
      <Header demo={demo} />
      {page}
      <Footer />
    </>
  );
}
