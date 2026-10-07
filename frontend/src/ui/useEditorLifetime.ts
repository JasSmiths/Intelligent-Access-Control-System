import React from "react";

// Reads can be aborted on disposal. Mutations retain their server result; callers
// check isCurrent before publishing feedback or beginning the next operation.
export function useEditorLifetime(identity?: unknown) {
  const lifetime = React.useMemo(() => {
    let active = false;
    const reads = new Set<AbortController>();
    return {
      activate() { active = true; },
      isCurrent: () => active,
      async read<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
        if (!active) throw new DOMException("Editor disposed", "AbortError");
        const controller = new AbortController(); reads.add(controller);
        try {
          const result = await operation(controller.signal);
          if (controller.signal.aborted || !active) throw new DOMException("Editor disposed", "AbortError");
          return result;
        }
        finally { reads.delete(controller); }
      },
      dispose() { active = false; for (const read of reads) read.abort(); reads.clear(); }
    };
  }, [identity]);
  React.useLayoutEffect(() => { lifetime.activate(); return () => lifetime.dispose(); }, [lifetime]);
  return lifetime;
}
