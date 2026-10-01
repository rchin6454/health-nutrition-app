// The only module that calls the backend. The browser never talks to the model provider.
import type { ChatResponse } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type StoredUserMessage = {
  role: "user";
  request_id: string;
  created_at: string;
  text: string;
};

export type StoredAssistantMessage = {
  role: "assistant";
  request_id: string;
  created_at: string;
  response: ChatResponse;
};

export type Conversation = {
  conversation_id: string;
  messages: (StoredUserMessage | StoredAssistantMessage)[];
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** HTTP 429 from the backend's per-IP limit. `retryAfterS`: when sending works again. */
export class RateLimitError extends ApiError {
  constructor(readonly retryAfterS: number) {
    super(
      `Too many requests. Please wait about ${retryAfterS} seconds before asking again.`,
      429,
    );
    this.name = "RateLimitError";
  }
}

const DEFAULT_RETRY_AFTER_S = 60;

function retryAfterSeconds(res: Response): number {
  const seconds = Number(res.headers.get("Retry-After"));
  return Number.isFinite(seconds) && seconds > 0
    ? Math.ceil(seconds)
    : DEFAULT_RETRY_AFTER_S;
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new ApiError("Could not reach the server. Check your connection.");
  }
  return res;
}

export async function sendMessage(
  conversationId: string,
  message: string,
): Promise<ChatResponse> {
  const res = await request("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversation_id: conversationId, message }),
  });
  if (res.status === 429) throw new RateLimitError(retryAfterSeconds(res));
  if (!res.ok) {
    throw new ApiError(
      `The server returned an error (HTTP ${res.status}).`,
      res.status,
    );
  }
  return (await res.json()) as ChatResponse;
}

/** Returns `null` if the conversation doesn't exist yet (nothing has been sent). */
export async function getConversation(
  conversationId: string,
): Promise<Conversation | null> {
  const res = await request(`/api/conversations/${conversationId}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new ApiError(
      `Could not load the conversation (HTTP ${res.status}).`,
      res.status,
    );
  }
  return (await res.json()) as Conversation;
}

export async function deleteConversation(
  conversationId: string,
): Promise<void> {
  const res = await request(`/api/conversations/${conversationId}`, {
    method: "DELETE",
  });
  if (!res.ok && res.status !== 404) {
    throw new ApiError(
      `Could not delete the conversation (HTTP ${res.status}).`,
      res.status,
    );
  }
}
