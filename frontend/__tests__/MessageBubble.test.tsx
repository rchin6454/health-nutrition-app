import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import MessageBubble from "@/components/MessageBubble";
import { answerResponse, errorResponse } from "./fixtures";

test("renders the answer as Markdown, then the claims as bullets", () => {
  render(
    <MessageBubble
      item={{ kind: "assistant", id: "a", response: answerResponse() }}
    />,
  );

  const strong = screen.getByText("Brown rice has more fibre");
  expect(strong.tagName).toBe("STRONG");

  const claims = screen.getAllByRole("listitem").map((li) => li.textContent);
  expect(claims).toEqual([
    "Brown rice keeps its bran layer.",
    "White rice has the bran removed.",
  ]);
});

test("renders an error response with its request_id", () => {
  render(
    <MessageBubble
      item={{ kind: "assistant", id: "e", response: errorResponse() }}
    />,
  );

  const alert = screen.getByRole("alert");
  expect(within(alert).getByText("Reference: req-err-42")).toBeDefined();
  expect(within(alert).queryByRole("listitem")).toBeNull();
});

test("does not render raw HTML from the answer", () => {
  const response = answerResponse({
    answer: '<img src=x onerror="alert(1)"> hi',
  });
  const { container } = render(
    <MessageBubble item={{ kind: "assistant", id: "x", response }} />,
  );
  expect(container.querySelector("img")).toBeNull();
});
