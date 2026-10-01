import ReactMarkdown from "react-markdown";
import CategoryBadge from "@/components/CategoryBadge";
import { DISCLAIMER, isEmergencyNotice } from "@/lib/notices";
import type { ChatResponse } from "@/lib/types";

export type ChatItem =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; response: ChatResponse }
  // The request never produced a ChatResponse (network failure, HTTP error).
  | { kind: "client_error"; id: string; text: string };

export default function MessageBubble({ item }: { item: ChatItem }) {
  if (item.kind === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-emerald-600 px-4 py-2 whitespace-pre-wrap text-white">
          {item.text}
        </p>
      </div>
    );
  }

  if (item.kind === "client_error") {
    return (
      <div
        role="alert"
        className={`${bubble} border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950`}
      >
        <p className="text-red-800 dark:text-red-200">{item.text}</p>
      </div>
    );
  }

  const { response } = item;
  if (response.answer_type === "error") {
    return (
      <div
        role="alert"
        className={`${bubble} border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950`}
      >
        <CategoryBadge response={response} />
        <p className="mt-2 text-red-800 dark:text-red-200">{response.answer}</p>
        <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-300">
          Reference: {response.request_id}
        </p>
        <Notices notices={response.notices} />
      </div>
    );
  }

  return (
    <div
      className={`${bubble} border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900`}
    >
      <CategoryBadge response={response} />
      <div className="markdown mt-2">
        <ReactMarkdown>{response.answer}</ReactMarkdown>
      </div>
      {response.claims.length > 0 && (
        <div className="mt-3 border-t border-zinc-200 pt-3 dark:border-zinc-800">
          <h3 className="text-xs font-semibold tracking-wide text-zinc-500 uppercase">
            Claims
          </h3>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-zinc-700 dark:text-zinc-300">
            {response.claims.map((claim, i) => (
              <li key={i}>{claim.text}</li>
            ))}
          </ul>
        </div>
      )}
      <Notices notices={response.notices} />
    </div>
  );
}

// Code-owned notices from the backend. The disclaimer is already in the page footer.
function Notices({ notices }: { notices: string[] }) {
  const shown = notices.filter((n) => n !== DISCLAIMER);
  if (shown.length === 0) return null;
  return (
    <ul aria-label="Notices" className="mt-3 space-y-2 text-sm">
      {shown.map((notice) =>
        isEmergencyNotice(notice) ? (
          <li
            key={notice}
            className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 font-medium text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200"
          >
            <p role="alert">{notice}</p>
          </li>
        ) : (
          <li
            key={notice}
            className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200"
          >
            {notice}
          </li>
        ),
      )}
    </ul>
  );
}

const bubble = "max-w-[85%] rounded-2xl rounded-bl-sm border px-4 py-3";
