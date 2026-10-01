"use client";

import { useEffect, useRef } from "react";
import MessageBubble, { type ChatItem } from "@/components/MessageBubble";
import SuggestedPrompts from "@/components/SuggestedPrompts";

type Props = {
  items: ChatItem[];
  sending: boolean;
  onSelectPrompt: (prompt: string) => void;
  onRetry: (message: string) => void;
  retryDisabled: boolean;
};

// New replies are announced by ChatPanel's live region, not by this list, so screen readers
// don't re-read the user's own messages.
export default function MessageList({
  items,
  sending,
  onSelectPrompt,
  onRetry,
  retryDisabled,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items.length, sending]);

  return (
    <div className="flex-1 overflow-y-auto p-3 md:p-4">
      {items.length === 0 && !sending ? (
        <SuggestedPrompts onSelect={onSelectPrompt} />
      ) : (
        <ol aria-label="Messages" className="flex flex-col gap-4">
          {items.map((item) => (
            <li key={item.id}>
              <MessageBubble
                item={item}
                onRetry={onRetry}
                retryDisabled={retryDisabled}
              />
            </li>
          ))}
          {sending && (
            <li className="text-sm text-zinc-500 italic" aria-hidden="true">
              Thinking…
            </li>
          )}
        </ol>
      )}
      <div ref={endRef} />
    </div>
  );
}
