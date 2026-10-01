import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import Home from "@/app/page";
import { CONVERSATION_KEY } from "@/components/ChatPanel";
import SourcesPanel from "@/components/SourcesPanel";
import { SUGGESTED_PROMPTS } from "@/components/SuggestedPrompts";
import * as api from "@/lib/api";
import { answerResponse, errorResponse } from "./fixtures";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof api>();
  return {
    ...actual,
    sendMessage: vi.fn(),
    getConversation: vi.fn(),
    deleteConversation: vi.fn(),
  };
});

const sendMessage = vi.mocked(api.sendMessage);
const getConversation = vi.mocked(api.getConversation);

beforeEach(() => {
  sendMessage.mockReset();
  getConversation.mockReset().mockResolvedValue(null);
});

function sourcesPanel() {
  return screen.getByRole("complementary", { name: "Sources" });
}

function expectSourcesEmpty() {
  const panel = sourcesPanel();
  expect(within(panel).getByText("No sources to show.")).toBeDefined();
  expect(within(panel).queryAllByRole("listitem")).toHaveLength(0);
  expect(panel.textContent).toBe("SourcesNo sources to show.");
}

async function ask(question: string) {
  const box = await screen.findByLabelText("Your question");
  await waitFor(() =>
    expect(screen.queryByText("Loading conversation…")).toBeNull(),
  );
  fireEvent.change(box, { target: { value: question } });
  fireEvent.keyDown(box, { key: "Enter" });
}

test("SourcesPanel renders only its empty state", () => {
  render(<SourcesPanel />);
  expectSourcesEmpty();
});

test("page shows title, message area, input, sources panel and disclaimer", async () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", {
      level: 1,
      name: "Food & Nutrition Assistant",
    }),
  ).toBeDefined();
  expect(await screen.findByText(SUGGESTED_PROMPTS[0])).toBeDefined();
  expect(screen.getByLabelText("Your question")).toBeDefined();
  expectSourcesEmpty();
  expect(
    screen.getByText("General information, not medical advice."),
  ).toBeDefined();
});

test("sources panel stays empty after an answer with claims arrives", async () => {
  sendMessage.mockResolvedValue(answerResponse());
  render(<Home />);
  expectSourcesEmpty();

  await ask("Is brown rice healthier than white rice?");

  expect(await screen.findByText("Brown rice has more fibre")).toBeDefined();
  expect(screen.getByText("Brown rice keeps its bran layer.")).toBeDefined();
  expectSourcesEmpty();
});

test("sends with the stored conversation id and shows Thinking… while waiting", async () => {
  localStorage.setItem(CONVERSATION_KEY, "conv-stored");
  let resolve: (r: ReturnType<typeof answerResponse>) => void = () => {};
  sendMessage.mockReturnValue(new Promise((r) => (resolve = r)));
  render(<Home />);

  await ask("Calories in rice?");

  expect(await (await messages()).findByText("Thinking…")).toBeDefined();
  expect(screen.getByRole("button", { name: "Send" })).toHaveProperty(
    "disabled",
    true,
  );
  expect(sendMessage).toHaveBeenCalledWith("conv-stored", "Calories in rice?");

  resolve(answerResponse());
  await waitFor(() =>
    expect(document.querySelector("li[aria-hidden]")).toBeNull(),
  );
});

test("clicking a suggested prompt sends it", async () => {
  sendMessage.mockResolvedValue(answerResponse());
  render(<Home />);
  fireEvent.click(await screen.findByText(SUGGESTED_PROMPTS[3]));
  await waitFor(() =>
    expect(sendMessage).toHaveBeenCalledWith(
      expect.any(String),
      SUGGESTED_PROMPTS[3],
    ),
  );
});

test("error responses show a bubble with the request_id", async () => {
  sendMessage.mockResolvedValue(errorResponse());
  render(<Home />);
  await ask("Is brown rice healthier than white rice?");
  expect(await screen.findByText("Reference: req-err-42")).toBeDefined();
});

test("network failures show an error bubble", async () => {
  sendMessage.mockRejectedValue(
    new api.ApiError("Could not reach the server."),
  );
  render(<Home />);
  await ask("Is brown rice healthier than white rice?");
  expect(
    await (await messages()).findByText("Could not reach the server."),
  ).toBeDefined();
});

test("reload restores the stored conversation from the backend", async () => {
  localStorage.setItem(CONVERSATION_KEY, "conv-stored");
  getConversation.mockResolvedValue({
    conversation_id: "conv-stored",
    messages: [
      {
        role: "user",
        request_id: "r1",
        created_at: "",
        text: "Earlier question",
      },
      {
        role: "assistant",
        request_id: "r1",
        created_at: "",
        response: answerResponse(),
      },
    ],
  });
  render(<Home />);

  expect(await screen.findByText("Earlier question")).toBeDefined();
  expect(screen.getByText("Brown rice keeps its bran layer.")).toBeDefined();
  expect(getConversation).toHaveBeenCalledWith("conv-stored");
  expectSourcesEmpty();
});

test("New chat starts a fresh conversation id and clears messages", async () => {
  localStorage.setItem(CONVERSATION_KEY, "conv-old");
  getConversation.mockResolvedValue({
    conversation_id: "conv-old",
    messages: [
      { role: "user", request_id: "r1", created_at: "", text: "Old question" },
    ],
  });
  render(<Home />);
  await screen.findByText("Old question");

  fireEvent.click(screen.getByRole("button", { name: "New chat" }));

  expect(screen.queryByText("Old question")).toBeNull();
  const newId = localStorage.getItem(CONVERSATION_KEY);
  expect(newId).not.toBe("conv-old");
  expect(newId).toMatch(/^[0-9a-f-]{36}$/);
});

test("a first visit creates and stores a conversation id", async () => {
  render(<Home />);
  await screen.findByText(SUGGESTED_PROMPTS[0]);
  expect(localStorage.getItem(CONVERSATION_KEY)).toMatch(/^[0-9a-f-]{36}$/);
  expect(getConversation).not.toHaveBeenCalled();
});

test("the live region announces each reply", async () => {
  sendMessage.mockResolvedValue(answerResponse());
  render(<Home />);
  await ask("Is brown rice healthier than white rice?");

  await waitFor(() =>
    expect(liveRegion().textContent).toBe(
      "The assistant replied: Brown rice has more fibre than white rice.",
    ),
  );
});

test("a rate-limited send shows a wait notice and blocks sending until it expires", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    sendMessage.mockRejectedValueOnce(new api.RateLimitError(3));
    render(<Home />);
    await ask("Is brown rice healthier than white rice?");

    expect(
      await (
        await messages()
      ).findByText(
        "Too many requests. Please wait about 3 seconds before asking again.",
      ),
    ).toBeDefined();
    expect(screen.getByText(/you can send again in [1-3] s\./)).toBeDefined();
    const box = screen.getByLabelText("Your question");
    fireEvent.change(box, { target: { value: "Another question" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(sendMessage).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Try again" })).toHaveProperty(
      "disabled",
      true,
    );

    await vi.advanceTimersByTimeAsync(4000);
    await waitFor(() =>
      expect(screen.queryByText(/you can send again/)).toBeNull(),
    );
    sendMessage.mockResolvedValue(answerResponse());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() =>
      expect(sendMessage).toHaveBeenLastCalledWith(
        expect.any(String),
        "Is brown rice healthier than white rice?",
      ),
    );
  } finally {
    vi.useRealTimers();
  }
});

test("Try again resends the failed message", async () => {
  sendMessage
    .mockRejectedValueOnce(new api.ApiError("Could not reach the server."))
    .mockResolvedValueOnce(answerResponse());
  render(<Home />);
  await ask("Calories in rice?");

  fireEvent.click(await screen.findByRole("button", { name: "Try again" }));

  expect(await screen.findByText("Brown rice has more fibre")).toBeDefined();
  expect(sendMessage).toHaveBeenNthCalledWith(
    2,
    expect.any(String),
    "Calories in rice?",
  );
});

test("focus moves to the question box after a suggested prompt is clicked", async () => {
  sendMessage.mockResolvedValue(answerResponse());
  render(<Home />);
  fireEvent.click(await screen.findByText(SUGGESTED_PROMPTS[0]));
  expect(document.activeElement).toBe(screen.getByLabelText("Your question"));
});

async function messages() {
  return within(await screen.findByRole("list", { name: "Messages" }));
}

function liveRegion() {
  const region = document.querySelector('[aria-live="polite"]');
  if (!region) throw new Error("no live region");
  return region;
}
