import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import CategoryBadge from "@/components/CategoryBadge";
import type { ChatResponse } from "@/lib/types";
import { answerResponse } from "./fixtures";

test.each<[Partial<ChatResponse>, string]>([
  [{ answer_type: "answer", category: "nutrition" }, "Nutrition"],
  [{ answer_type: "answer", category: "food_safety" }, "Food Safety"],
  [{ answer_type: "answer", category: "general_food" }, "General Food"],
  [{ answer_type: "answer", category: "mixed" }, "Nutrition + Food Safety"],
  [
    { answer_type: "clarification", category: "food_safety" },
    "Needs more detail",
  ],
  [{ answer_type: "out_of_scope", category: "out_of_scope" }, "Out of scope"],
  [{ answer_type: "error", category: "none" }, "Error"],
])("%o → %s", (overrides, label) => {
  const { container } = render(
    <CategoryBadge response={answerResponse(overrides)} />,
  );
  expect(screen.getByText(label)).toBeDefined();
  expect(container.textContent).toBe(label);
});

test("an answer without a category shows no badge", () => {
  const { container } = render(
    <CategoryBadge response={answerResponse({ category: "none" })} />,
  );
  expect(container.textContent).toBe("");
});
