"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ChatInput from "@/components/ChatInput";
import type { ChatItem } from "@/components/MessageBubble";
import MessageList from "@/components/MessageList";
import {
  ApiError,
  getConversation,
  RateLimitError,
  sendMessage,
  type Conversation,
} from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

export const CONVERSATION_KEY = "conversation_id";

function readStoredId(): string | null {
  try {
    return localStorage.getItem(CONVERSATION_KEY);
  } catch {
    return null;
  }
}

function startConversation(): string {
  const id = crypto.randomUUID();
  try {
    localStorage.setItem(CONVERSATION_KEY, id);
  } catch {
    // Storage unavailable (private mode): the chat still works, it just won't survive a reload.
  }
  return id;
}

function toItems(conversation: Conversation): ChatItem[] {
  return conversation.messages.map((m) =>
    m.role === "user"
      ? { kind: "user", id: `${m.request_id}-user`, text: m.text }
      : {
          kind: "assistant",
          id: `${m.request_id}-assistant`,
          response: m.response,
        },
  );
}

function errorText(err: unknown): string {
  return err instanceof ApiError
    ? err.message
    : "Something went wrong. Please try again.";
}

// What the live region reads out when a reply arrives.
function announcement(response: ChatResponse): string {
  const label =
    response.answer_type === "error"
      ? "Error"
      : response.answer_type === "clarification"
        ? "The assistant asks"
        : "The assistant replied";
  // Markdown markers (**bold**, # headings, list bullets) would be read out literally.
  const text = response.answer.replace(/[*_#`>]+/g, "").replace(/\s+/g, " ");
  return `${label}: ${text.trim()}`;
}

// A rate-limit cooldown: `start(seconds)` begins it; `remaining` counts down to 0 each second.
function useCooldown(): [number, (seconds: number) => void] {
  const [until, setUntil] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const start = useCallback((seconds: number) => {
    const t = Date.now();
    setNow(t);
    setUntil(t + seconds * 1000);
  }, []);
  useEffect(() => {
    if (until === null) return;
    const timer = setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= until) setUntil(null);
    }, 1000);
    return () => clearInterval(timer);
  }, [until]);
  const remaining =
    until === null ? 0 : Math.max(0, Math.ceil((until - now) / 1000));
  return [remaining, start];
}

export default function ChatPanel() {
  const conversationId = useRef<string | null>(null);
  const [items, setItems] = useState<ChatItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [cooldown, startCooldown] = useCooldown();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Restore the stored conversation from the backend on first load.
  useEffect(() => {
    let cancelled = false;
    const storedId = readStoredId();
    conversationId.current = storedId ?? startConversation();

    (async () => {
      try {
        if (storedId) {
          const conversation = await getConversation(storedId);
          if (!cancelled && conversation) setItems(toItems(conversation));
        }
      } catch (err) {
        if (!cancelled) {
          setItems([
            {
              kind: "client_error",
              id: "history-error",
              text: `Couldn't load your previous conversation. ${errorText(err)}`,
            },
          ]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  async function send(message: string) {
    const id = conversationId.current ?? startConversation();
    conversationId.current = id;
    // Clicking a suggested prompt or "Try again" removes that button: keep focus in the box.
    inputRef.current?.focus();
    setItems((prev) => [
      ...prev,
      { kind: "user", id: crypto.randomUUID(), text: message },
    ]);
    setSending(true);
    setLiveText("Thinking…");
    try {
      const response = await sendMessage(id, message);
      setItems((prev) => [
        ...prev,
        { kind: "assistant", id: response.request_id, response },
      ]);
      setLiveText(announcement(response));
    } catch (err) {
      const rateLimited = err instanceof RateLimitError;
      if (rateLimited) startCooldown(err.retryAfterS);
      const text = errorText(err);
      setItems((prev) => [
        ...prev,
        {
          kind: "client_error",
          id: crypto.randomUUID(),
          text,
          retry: message,
          rateLimited,
        },
      ]);
      setLiveText(text);
    } finally {
      setSending(false);
    }
  }

  function newChat() {
    conversationId.current = startConversation();
    setItems([]);
    setLiveText("");
    inputRef.current?.focus();
  }

  const blocked = sending || loading || cooldown > 0;

  return (
    <section
      aria-label="Chat"
      className="flex min-h-0 flex-1 flex-col rounded-xl border border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950"
    >
      <div className="flex items-center justify-end border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <button
          type="button"
          onClick={newChat}
          disabled={sending || loading}
          className="rounded-lg border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-emerald-600 disabled:opacity-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
        >
          New chat
        </button>
      </div>
      {loading ? (
        <p className="flex-1 p-4 text-sm text-zinc-500" role="status">
          Loading conversation…
        </p>
      ) : (
        <MessageList
          items={items}
          sending={sending}
          onSelectPrompt={send}
          onRetry={send}
          retryDisabled={blocked}
        />
      )}
      <ChatInput
        onSend={send}
        disabled={blocked}
        status={
          cooldown > 0
            ? `Too many requests — you can send again in ${cooldown} s.`
            : undefined
        }
        inputRef={inputRef}
      />
      <p role="status" aria-live="polite" className="sr-only">
        {liveText}
      </p>
    </section>
  );
}
