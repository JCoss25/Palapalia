/**
 * Hook mínimo de datos asíncronos.
 *
 * No se usa TanStack Query en la web a propósito: son seis pantallas y el bundle pesa. Aquí basta
 * con estado, cancelación por `AbortController` y una función `reload` para los reintentos.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: unknown;
  /** `true` si el dato vino del respaldo local y no de la API. */
  fromFallback: boolean;
  reload: () => void;
}

export function useAsync<T>(
  factory: (signal: AbortSignal) => Promise<{ data: T; fromFallback: boolean }>,
  deps: readonly unknown[],
): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [fromFallback, setFromFallback] = useState(false);
  const [nonce, setNonce] = useState(0);
  const factoryRef = useRef(factory);
  factoryRef.current = factory;

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setLoading(true);
    setError(null);

    factoryRef
      .current(controller.signal)
      .then((result) => {
        if (!alive) return;
        setData(result.data);
        setFromFallback(result.fromFallback);
      })
      .catch((caught: unknown) => {
        if (!alive || controller.signal.aborted) return;
        setError(caught);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });

    return () => {
      alive = false;
      controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, loading, error, fromFallback, reload };
}
