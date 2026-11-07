"use client";

import * as React from "react";

import { API_BASE } from "@/lib/api/client";

type Frame = Record<string, unknown>;

export function useRunStream(options: {
  runId: string | null;
  after?: number;
  epoch?: number;
  enabled?: boolean;
  onToken?: (delta: string) => void;
  onEvent?: (event: string, payload: Frame) => void;
  onTerminal?: (event: string, payload: Frame) => void;
}) {
  const {
    runId,
    after = 0,
    epoch = 0,
    enabled = true,
    onToken,
    onEvent,
    onTerminal,
  } = options;
  const handlers = React.useRef({ onToken, onEvent, onTerminal });
  handlers.current = { onToken, onEvent, onTerminal };

  React.useEffect(() => {
    if (!enabled || !runId) return;
    const url = `${API_BASE}/runs/${runId}/events?after=${after}&epoch=${epoch}`;
    const source = new EventSource(url, { withCredentials: true });

    const terminal = new Set(["chat.done", "chat.error", "chat.cancelled"]);
    const names = [
      "token",
      "run.state",
      "chat.tool_call",
      "chat.tool_result",
      "chat.citation",
      "chat.done",
      "chat.error",
      "chat.cancelled",
      "degraded",
      "heartbeat",
    ];

    const listeners: Array<[string, (event: Event) => void]> = names.map((name) => {
      const listener = (event: Event) => {
        const message = event as MessageEvent<string>;
        let payload: Frame = {};
        try {
          payload = JSON.parse(message.data) as Frame;
        } catch {
          return;
        }
        if (name === "token") {
          handlers.current.onToken?.(String(payload.delta ?? ""));
        } else {
          handlers.current.onEvent?.(name, payload);
        }
        if (terminal.has(name)) {
          handlers.current.onTerminal?.(name, payload);
          source.close();
        }
      };
      source.addEventListener(name, listener);
      return [name, listener];
    });

    return () => {
      for (const [name, listener] of listeners) {
        source.removeEventListener(name, listener);
      }
      source.close();
    };
  }, [runId, after, epoch, enabled]);
}
