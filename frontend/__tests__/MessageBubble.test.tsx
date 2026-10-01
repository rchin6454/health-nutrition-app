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

const EMERGENCY =
  "This may be a medical emergency. Call 112 (emergency) or 108 (ambulance) now, or go to the nearest hospital.";
const HIGH_RISK =
  "Pregnant women, infants and young children, older adults and people with weak immunity are more vulnerable.";

test("renders the category badge", () => {
  render(
    <MessageBubble
      item={{
        kind: "assistant",
        id: "b",
        response: answerResponse({ category: "food_safety" }),
      }}
    />,
  );
  expect(screen.getByText("Food Safety")).toBeDefined();
});

test("renders notices below the answer, highlighting emergency notices", () => {
  render(
    <MessageBubble
      item={{
        kind: "assistant",
        id: "n",
        response: answerResponse({
          notices: [
            EMERGENCY,
            HIGH_RISK,
            "General information, not medical advice.",
          ],
        }),
      }}
    />,
  );

  const notices = screen.getByRole("list", { name: "Notices" });
  const items = within(notices).getAllByRole("listitem");
  // The disclaimer is left to the page footer.
  expect(items.map((li) => li.textContent)).toEqual([EMERGENCY, HIGH_RISK]);
  expect(screen.getByRole("alert").textContent).toBe(EMERGENCY);
});

test("the disclaimer alone renders no notices list", () => {
  render(
    <MessageBubble
      item={{ kind: "assistant", id: "d", response: answerResponse() }}
    />,
  );
  expect(screen.queryByRole("list", { name: "Notices" })).toBeNull();
});

test("renders a clarification as plain text without claims", () => {
  render(
    <MessageBubble
      item={{
        kind: "assistant",
        id: "c",
        response: answerResponse({
          answer_type: "clarification",
          category: "food_safety",
          answer: "Which food do you mean, and how was it stored?",
          claims: [],
        }),
      }}
    />,
  );
  expect(screen.getByText("Needs more detail")).toBeDefined();
  expect(
    screen.getByText("Which food do you mean, and how was it stored?"),
  ).toBeDefined();
  expect(screen.queryByText("Claims")).toBeNull();
});
