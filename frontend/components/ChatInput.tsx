"use client";

import { useState, type FormEvent, type KeyboardEvent } from "react";

export const MAX_MESSAGE_CHARS = 1000;

type Props = {
  onSend: (message: string) => void;
  disabled: boolean;
};

export default function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState("");
  const canSend = !disabled && value.trim().length > 0;

  function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter adds a new line; don't interrupt IME composition.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex items-end gap-2 border-t border-zinc-200 p-3 dark:border-zinc-800"
    >
      <div className="flex flex-1 flex-col">
        <label htmlFor="chat-input" className="sr-only">
          Your question
        </label>
        <textarea
          id="chat-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={disabled}
          maxLength={MAX_MESSAGE_CHARS}
          rows={2}
          placeholder="Ask about food, nutrition or safety…"
          className="resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 focus:border-emerald-600 focus:outline-none disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950"
        />
        <span className="mt-1 self-end text-xs text-zinc-500">
          {value.length}/{MAX_MESSAGE_CHARS}
        </span>
      </div>
      <button
        type="submit"
        disabled={!canSend}
        className="mb-5 rounded-lg bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-600 disabled:opacity-50"
      >
        Send
      </button>
    </form>
  );
}
