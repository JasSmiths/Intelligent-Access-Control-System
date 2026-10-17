import React from "react";
import { createRoot, type Root } from "react-dom/client";
import { ConfirmDialog, type ConfirmDialogProps } from "./ConfirmDialog";

export type ConfirmOptions = Omit<ConfirmDialogProps, "busy" | "onResult" | "onActionError">;
export type Confirm = (options: ConfirmOptions) => Promise<boolean>;

type ConfirmRequest = { id: number; options: ConfirmOptions; settle: (confirmed: boolean) => void; fail: (error: unknown) => void };

// One app-wide host owns the in-app confirmation surface; requests queue so only one is visible.
// The host exists only while a request is queued. Modals isolate the body children present when
// they open (useModalFocus marks them inert), so a host left mounted between prompts would be
// inert under the next editor and its buttons would ignore clicks. A fresh host is appended last.
let queue: readonly ConfirmRequest[] = [];
let nextId = 1;
let host: { root: Root; container: HTMLElement } | null = null;
const listeners = new Set<() => void>();
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const snapshot = () => queue;
const publish = (next: readonly ConfirmRequest[]) => { queue = next; listeners.forEach((listener) => listener()); };

function ConfirmHost() {
  const requests = React.useSyncExternalStore(subscribe, snapshot, snapshot);
  const current = requests[0];
  if (!current) return null;
  return <ConfirmDialog key={current.id} {...current.options} onResult={current.settle} onActionError={current.fail} />;
}

function releaseHost() {
  const current = host;
  host = null;
  if (current) { current.root.unmount(); current.container.remove(); }
}

function ensureHost() {
  if (host?.container.isConnected) return;
  releaseHost();
  const container = document.createElement("div");
  container.dataset.confirmHost = "";
  document.body.appendChild(container);
  const root = createRoot(container);
  root.render(<ConfirmHost />);
  host = { root, container };
}

function request(options: ConfirmOptions, owner?: Set<ConfirmRequest>) {
  return new Promise<boolean>((resolve, reject) => {
    const entry: ConfirmRequest = {
      id: nextId++,
      options,
      settle: (confirmed) => { if (!remove(entry)) return; resolve(confirmed); },
      fail: (error) => { if (!remove(entry)) return; reject(error); },
    };
    const remove = (item: ConfirmRequest) => {
      if (!queue.includes(item)) return false;
      owner?.delete(item);
      publish(queue.filter((candidate) => candidate !== item));
      if (!queue.length) {
        // Unmount after the settling event or effect finishes, unless a new request arrived meanwhile.
        const idle = host;
        queueMicrotask(() => { if (!queue.length && host === idle) releaseHost(); });
      }
      return true;
    };
    owner?.add(entry);
    ensureHost();
    publish([...queue, entry]);
  });
}

/**
 * Promise-based in-app confirmation. Resolves true when confirmed and false when
 * cancelled (Cancel, Escape, or the calling component unmounting). With `action`,
 * the dialog stays open and busy until it settles; a rejection rejects the promise.
 */
export function useConfirm(): Confirm {
  const owned = React.useRef(new Set<ConfirmRequest>());
  React.useEffect(() => {
    const requests = owned.current;
    return () => { for (const entry of [...requests]) entry.settle(false); };
  }, []);
  return React.useCallback((options: ConfirmOptions) => request(options, owned.current), []);
}

/** Cancels every pending confirmation and unmounts the host. Used by test cleanup. */
export function disposeConfirmHost() {
  for (const entry of [...queue]) entry.settle(false);
  releaseHost();
}
