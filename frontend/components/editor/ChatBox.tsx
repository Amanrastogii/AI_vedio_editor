"use client";

import { useEffect, useRef, useState } from "react";
import { ChatMessage, getChatHistory, sendChatMessage } from "@/lib/api";

interface Props {
  projectId: string;
  onApplied: () => void; // called after an assistant reply that changed the timeline
}

const EXAMPLES = [
  "remove clip 2",
  "move clip 3 to position 1",
  "trim clip 2 start to 5s",
  "change transition of clip 2 to fade",
];

export default function ChatBox({ projectId, onApplied }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatHistory(projectId).then(setMessages).catch(() => {});
  }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function send(text?: string) {
    const message = (text ?? input).trim();
    if (!message || sending) return;
    setInput("");
    setSending(true);
    setMessages((prev) => [
      ...prev,
      { id: `local-${Date.now()}`, role: "user", content: message, action_json: null, created_at: new Date().toISOString() },
    ]);
    try {
      const reply = await sendChatMessage(projectId, message);
      setMessages((prev) => [...prev, reply]);
      if (reply.action_json) onApplied();
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { id: `err-${Date.now()}`, role: "assistant", content: e.message || "Failed to send.", action_json: null, created_at: new Date().toISOString() },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="glass flex h-full flex-col rounded-2xl">
      <div className="border-b border-border px-3 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Ask the AI to edit
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto px-3 py-3" style={{ minHeight: 200, maxHeight: 320 }}>
        {messages.length === 0 && (
          <div className="space-y-1.5 text-xs text-slate-500">
            <p>Try one of these:</p>
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                onClick={() => send(ex)}
                className="block w-full rounded border border-border bg-surface2 px-2 py-1 text-left text-[11px] text-slate-300 hover:border-accent"
              >
                {ex}
              </button>
            ))}
          </div>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`max-w-[85%] rounded-lg px-2.5 py-1.5 text-xs ${
              m.role === "user" ? "ml-auto bg-accent/20 text-white" : "bg-surface2 text-slate-300"
            }`}
          >
            {m.content}
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2 border-t border-border p-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="e.g. remove clip 2"
          className="flex-1 rounded-lg border border-border bg-surface2 px-3 py-1.5 text-xs text-slate-200 outline-none focus:border-accent"
        />
        <button
          onClick={() => send()}
          disabled={sending || !input.trim()}
          className="rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
        >
          {sending ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
