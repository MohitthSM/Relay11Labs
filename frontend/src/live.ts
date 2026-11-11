import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { watchEvents } from "./api";
import type { SocketStatus } from "./api";
import type { StateMessage } from "./types";

type Listener = (m: StateMessage) => void;

/** The page's single WebSocket, shared by everything that listens (D8). */
export type Live = {
  status: SocketStatus;
  subscribe: (listener: Listener) => () => void;
};

export function useLive(): Live {
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const listeners = useRef(new Set<Listener>());

  useEffect(() => watchEvents((m) => listeners.current.forEach((fn) => fn(m)), setStatus), []);

  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  // a new object each render would make every effect that depends on it run again
  return useMemo(() => ({ status, subscribe }), [status, subscribe]);
}
