"use client";

import { useEffect, useRef } from "react";
import MessageBubble, { type ChatItem } from "@/components/MessageBubble";
import SuggestedPrompts from "@/components/SuggestedPrompts";

type Props = {
  items: ChatItem[];
  sending: boolean;
  onSelectPrompt: (prompt: string) => void;
};

export default function MessageList({ items, sending, onSelectPrompt }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [items.length, sending]);

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {items.length === 0 && !sending ? (
        <SuggestedPrompts onSelect={onSelectPrompt} />
      ) : (
        <ol
          aria-live="polite"
          aria-label="Messages"
          className="flex flex-col gap-4"
        >
          {items.map((item) => (
            <li key={item.id}>
              <MessageBubble item={item} />
            </li>
          ))}
          {sending && (
            <li className="text-sm text-zinc-500 italic" role="status">
              Thinking…
            </li>
          )}
        </ol>
      )}
      <div ref={endRef} />
    </div>
  );
}
