import { afterEach, expect, test, vi } from "vitest";
import { ApiError, RateLimitError, sendMessage } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(response: Response) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

test("HTTP 429 becomes a RateLimitError with the Retry-After seconds", async () => {
  stubFetch(
    new Response(JSON.stringify({ detail: "Too many requests." }), {
      status: 429,
      headers: { "Retry-After": "17" },
    }),
  );
  const err = await sendMessage("c", "hi").catch((e: unknown) => e);
  expect(err).toBeInstanceOf(RateLimitError);
  expect((err as RateLimitError).retryAfterS).toBe(17);
  expect((err as RateLimitError).status).toBe(429);
});

test("a 429 without Retry-After waits a minute", async () => {
  stubFetch(new Response("", { status: 429 }));
  const err = await sendMessage("c", "hi").catch((e: unknown) => e);
  expect((err as RateLimitError).retryAfterS).toBe(60);
});

test("other HTTP errors stay plain ApiErrors", async () => {
  stubFetch(new Response("", { status: 500 }));
  const err = await sendMessage("c", "hi").catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiError);
  expect(err).not.toBeInstanceOf(RateLimitError);
});
