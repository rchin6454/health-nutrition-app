import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ChatInput, { MAX_MESSAGE_CHARS } from "@/components/ChatInput";

function setup(disabled = false) {
  const onSend = vi.fn();
  render(<ChatInput onSend={onSend} disabled={disabled} />);
  const box = screen.getByLabelText("Your question") as HTMLTextAreaElement;
  return { onSend, box };
}

test("Enter sends the trimmed message and clears the box", () => {
  const { onSend, box } = setup();
  fireEvent.change(box, { target: { value: "  Is dahi healthy?  " } });
  fireEvent.keyDown(box, { key: "Enter" });

  expect(onSend).toHaveBeenCalledWith("Is dahi healthy?");
  expect(box.value).toBe("");
});

test("Shift+Enter does not send", () => {
  const { onSend, box } = setup();
  fireEvent.change(box, { target: { value: "line one" } });
  fireEvent.keyDown(box, { key: "Enter", shiftKey: true });

  expect(onSend).not.toHaveBeenCalled();
});

test("blank messages are not sent", () => {
  const { onSend, box } = setup();
  fireEvent.change(box, { target: { value: "   " } });
  fireEvent.keyDown(box, { key: "Enter" });

  expect(onSend).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Send" })).toHaveProperty(
    "disabled",
    true,
  );
});

test("is disabled while sending", () => {
  const { box } = setup(true);
  expect(box.disabled).toBe(true);
  expect(screen.getByRole("button", { name: "Send" })).toHaveProperty(
    "disabled",
    true,
  );
});

test("limits input to 1,000 characters and shows the count", () => {
  const { box } = setup();
  expect(box.maxLength).toBe(MAX_MESSAGE_CHARS);
  expect(MAX_MESSAGE_CHARS).toBe(1000);
  fireEvent.change(box, { target: { value: "abc" } });
  expect(screen.getByText("3/1000")).toBeDefined();
});
