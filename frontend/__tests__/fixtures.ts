import type { ChatResponse } from "@/lib/types";

export function answerResponse(
  overrides: Partial<ChatResponse> = {},
): ChatResponse {
  return {
    schema_version: "1.0",
    request_id: "req-1",
    conversation_id: "conv-1",
    answer_type: "answer",
    category: "none",
    answer: "**Brown rice has more fibre** than white rice.",
    claims: [
      { text: "Brown rice keeps its bran layer.", source: null },
      { text: "White rice has the bran removed.", source: null },
    ],
    notices: ["General information, not medical advice."],
    ...overrides,
  };
}

export function errorResponse(): ChatResponse {
  return answerResponse({
    request_id: "req-err-42",
    answer_type: "error",
    answer: "Sorry, something went wrong. (Reference: req-err-42)",
    claims: [],
  });
}
