import { create } from 'zustand';

interface SessionState {
  accessToken: string | null;
  refreshToken: string | null;
  user: { id: string; role: string; display_name?: string | null } | null;
  setSession: (accessToken: string, refreshToken: string, user: SessionState['user']) => void;
  clear: () => void;
}

export const sessionStore = create<SessionState>((set) => ({
  accessToken: null,
  refreshToken: null,
  user: null,
  setSession: (accessToken, refreshToken, user) => set({ accessToken, refreshToken, user }),
  clear: () => set({ accessToken: null, refreshToken: null, user: null }),
}));

/** Estado del borrador de reserva: sobrevive a la navegación entre detalle → checkout. */
interface BookingDraftState {
  venueId: string | null;
  startAt: string | null;
  endAt: string | null;
  guests: number;
  extras: string[];
  setDraft: (draft: Partial<Omit<BookingDraftState, 'setDraft' | 'reset'>>) => void;
  reset: () => void;
}

export const bookingDraftStore = create<BookingDraftState>((set) => ({
  venueId: null,
  startAt: null,
  endAt: null,
  guests: 50,
  extras: [],
  setDraft: (draft) => set((state) => ({ ...state, ...draft })),
  reset: () => set({ venueId: null, startAt: null, endAt: null, guests: 50, extras: [] }),
}));
