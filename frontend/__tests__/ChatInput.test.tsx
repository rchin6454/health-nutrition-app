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

test("while disabled, the box stays editable but nothing is sent", () => {
  // Keeping the textarea enabled keeps keyboard focus in it while a reply is pending.
  const { onSend, box } = setup(true);
  expect(box.disabled).toBe(false);
  fireEvent.change(box, { target: { value: "Next question" } });
  fireEvent.keyDown(box, { key: "Enter" });

  expect(onSend).not.toHaveBeenCalled();
  expect(box.value).toBe("Next question");
  expect(screen.getByRole("button", { name: "Send" })).toHaveProperty(
    "disabled",
    true,
  );
});

test("shows the status under the box and links it for screen readers", () => {
  render(
    <ChatInput onSend={vi.fn()} disabled status="Wait 5 s." inputRef={null} />,
  );
  const box = screen.getByLabelText("Your question");
  const status = document.getElementById(
    box.getAttribute("aria-describedby") ?? "",
  );
  expect(status?.textContent).toContain("Wait 5 s.");
});

test("limits input to 1,000 characters and shows the count", () => {
  const { box } = setup();
  expect(box.maxLength).toBe(MAX_MESSAGE_CHARS);
  expect(MAX_MESSAGE_CHARS).toBe(1000);
  fireEvent.change(box, { target: { value: "abc" } });
  expect(screen.getByText("3/1000")).toBeDefined();
});
