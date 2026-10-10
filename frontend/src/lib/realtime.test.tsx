import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useRealtimeRefresh } from "./realtime";

describe("useRealtimeRefresh", () => {
  afterEach(() => vi.useRealTimers());

  it("refetches only for subscribed topics and authoritative resync", () => {
    vi.useFakeTimers();
    const refresh = vi.fn();
    renderHook(() => useRealtimeRefresh(["bookings.changed"], refresh));

    act(() => window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic: "inventory.changed" } })));
    act(() => vi.advanceTimersByTime(100));
    expect(refresh).not.toHaveBeenCalled();

    act(() => window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic: "bookings.changed" } })));
    act(() => vi.advanceTimersByTime(100));
    expect(refresh).toHaveBeenCalledTimes(1);

    act(() => window.dispatchEvent(new CustomEvent("os10:invalidate", { detail: { topic: "*" } })));
    act(() => vi.advanceTimersByTime(100));
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
