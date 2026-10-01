"use client";

import { useState, type FormEvent, type KeyboardEvent, type Ref } from "react";

export const MAX_MESSAGE_CHARS = 1000;

type Props = {
  onSend: (message: string) => void;
  // Sending is blocked (a reply is pending, or the rate limit is cooling down). The box stays
  // editable, so keyboard focus is never lost and the next question can be typed.
  disabled: boolean;
  // Why sending is blocked, shown under the box (e.g. the rate-limit countdown).
  status?: string;
  inputRef?: Ref<HTMLTextAreaElement>;
};

export default function ChatInput({
  onSend,
  disabled,
  status,
  inputRef,
}: Props) {
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
          ref={inputRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          aria-describedby="chat-input-status"
          maxLength={MAX_MESSAGE_CHARS}
          rows={2}
          placeholder="Ask about food, nutrition or safety…"
          className="resize-none rounded-lg border border-zinc-300 bg-white px-3 py-2 focus:border-emerald-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600/40 dark:border-zinc-700 dark:bg-zinc-950"
        />
        <div
          id="chat-input-status"
          className="mt-1 flex justify-between gap-2 text-xs text-zinc-500"
        >
          <span className="text-amber-700 dark:text-amber-400">{status}</span>
          <span>
            {value.length}/{MAX_MESSAGE_CHARS}
          </span>
        </div>
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
