import { useEffect, useRef } from "react";

/**
 * Opens a WebSocket via `connect()` and keeps it open with exponential
 * backoff reconnects while `enabled` is true. `connect` is called again on
 * every reconnect attempt so it can pick up a fresh token/URL if needed.
 */
export function useWebSocket(
  enabled: boolean,
  connect: () => WebSocket | null,
  onMessage: (data: any) => void,
  onTerminal?: () => void
) {
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stoppedRef = useRef(false);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    stoppedRef.current = false;
    if (!enabled) return;

    function open() {
      if (stoppedRef.current) return;
      const ws = connect();
      if (!ws) return;
      socketRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
      };
      ws.onmessage = (ev) => {
        try {
          onMessage(JSON.parse(ev.data));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        socketRef.current = null;
        if (stoppedRef.current) return;
        const delay = Math.min(1000 * 2 ** attemptRef.current, 15000);
        attemptRef.current += 1;
        timerRef.current = setTimeout(open, delay);
      };
      ws.onerror = () => {
        ws.close();
      };
    }

    open();

    return () => {
      stoppedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  function stop() {
    stoppedRef.current = true;
    if (timerRef.current) clearTimeout(timerRef.current);
    socketRef.current?.close();
    onTerminal?.();
  }

  return { stop };
}
