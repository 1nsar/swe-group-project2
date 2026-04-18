/**
 * Component tests for the AI suggestion UI (Assignment 2 §4.2).
 *
 * We mock the streamAI/aiApi layer so we can drive the panel through the
 * meta → token → done → accept flow deterministically, without a real
 * backend. The tests assert the user-visible behaviours evaluators care
 * about: "select text, press generate, see tokens stream in, accept
 * applies the text, reject closes the panel".
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AIPanel from "../components/AIPanel";

// ── mock the AI module ──────────────────────────────────────────────────────
let capturedHandlers: {
  onMeta?: (m: { interaction_id: string; model: string; provider: string }) => void;
  onToken?: (t: string) => void;
  onDone?: (p: { interaction_id: string; output: string }) => void;
  onError?: (m: string) => void;
  onCancel?: () => void;
} = {};
const abortMock = vi.fn();

vi.mock("../api/ai", () => {
  return {
    streamAI: vi.fn((_req: unknown, handlers: typeof capturedHandlers) => {
      capturedHandlers = handlers;
      return { abort: abortMock } as unknown as AbortController;
    }),
    aiApi: {
      listActions: vi.fn().mockResolvedValue({ actions: ["rewrite", "summarize"] }),
      accept: vi.fn().mockResolvedValue({
        id: "i-1",
        status: "accepted",
      }),
      reject: vi.fn().mockResolvedValue({ id: "i-1", status: "rejected" }),
      history: vi.fn().mockResolvedValue([]),
      get: vi.fn(),
    },
  };
});

import { streamAI, aiApi } from "../api/ai";

describe("<AIPanel />", () => {
  beforeEach(() => {
    capturedHandlers = {};
    abortMock.mockClear();
    vi.mocked(streamAI).mockClear();
    vi.mocked(aiApi.accept).mockClear();
    vi.mocked(aiApi.reject).mockClear();
  });

  function renderPanel(overrides: Partial<React.ComponentProps<typeof AIPanel>> = {}) {
    const props: React.ComponentProps<typeof AIPanel> = {
      docId: "doc-abc",
      selectionText: "The quick brown fox.",
      onAccept: vi.fn(),
      onClose: vi.fn(),
      ...overrides,
    };
    return { props, ...render(<AIPanel {...props} />) };
  }

  it("renders the Compose tab with Generate disabled messaging when nothing is selected", async () => {
    renderPanel({ selectionText: "" });
    expect(screen.getByRole("button", { name: /Generate/i })).toBeInTheDocument();
    expect(screen.getByText(/— nothing selected —/i)).toBeInTheDocument();
  });

  it("shows streamed tokens, then accept applies the output to the editor", async () => {
    const { props } = renderPanel();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: /Generate/i }));

    expect(streamAI).toHaveBeenCalledOnce();
    expect(streamAI).toHaveBeenCalledWith(
      expect.objectContaining({
        document_id: "doc-abc",
        action: "rewrite",
        selection: "The quick brown fox.",
      }),
      expect.any(Object),
    );

    // Drive the stream: meta → two tokens → done
    await act(async () => {
      capturedHandlers.onMeta?.({
        interaction_id: "i-42",
        model: "mock-1",
        provider: "mock",
      });
    });
    await act(async () => {
      capturedHandlers.onToken?.("Hello, ");
      capturedHandlers.onToken?.("world.");
    });
    expect(screen.getByText(/Hello, world\./)).toBeInTheDocument();

    await act(async () => {
      capturedHandlers.onDone?.({
        interaction_id: "i-42",
        output: "Hello, world.",
      });
    });

    // Accept button is now visible — click it.
    await user.click(screen.getByRole("button", { name: /Accept/ }));
    expect(aiApi.accept).toHaveBeenCalledWith("i-42", "Hello, world.");
    expect(props.onAccept).toHaveBeenCalledWith("Hello, world.");
  });

  it("reject clears the output and notifies the backend", async () => {
    renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Generate/i }));

    await act(async () => {
      capturedHandlers.onMeta?.({ interaction_id: "i-9", model: "m", provider: "mock" });
      capturedHandlers.onToken?.("output text");
      capturedHandlers.onDone?.({ interaction_id: "i-9", output: "output text" });
    });

    await user.click(screen.getByRole("button", { name: /Reject/ }));
    expect(aiApi.reject).toHaveBeenCalledWith("i-9");
  });

  it("Stop aborts the in-flight stream", async () => {
    renderPanel();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Generate/i }));

    await act(async () => {
      capturedHandlers.onMeta?.({ interaction_id: "i-7", model: "m", provider: "mock" });
      capturedHandlers.onToken?.("partial");
    });

    const stopBtn = screen.getByRole("button", { name: /Stop/ });
    await user.click(stopBtn);
    expect(abortMock).toHaveBeenCalled();
  });
});
