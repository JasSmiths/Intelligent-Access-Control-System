import { describe, expect, it } from "vitest";

import { CHAT_ATTACHMENT_MAX_BYTES, coerceSettingsPayload } from "./shared";
import { uploadChatAttachment } from "./views/ChatWidgetView";

describe("audit guardrails", () => {
  it("rejects non-finite numeric dynamic settings", () => {
    expect(() => coerceSettingsPayload({ lpr_debounce_quiet_seconds: "" })).toThrow(
      "lpr_debounce_quiet_seconds must be a finite number."
    );
    expect(() => coerceSettingsPayload({ lpr_similarity_threshold: "NaN" })).toThrow(
      "lpr_similarity_threshold must be a finite number."
    );
    expect(coerceSettingsPayload({ lpr_debounce_quiet_seconds: "2.5" })).toEqual({
      lpr_debounce_quiet_seconds: 2.5
    });
  });

  it("rejects chat attachments over 25 MB before upload", async () => {
    const file = new File([new Uint8Array(CHAT_ATTACHMENT_MAX_BYTES + 1)], "oversized.bin");

    await expect(uploadChatAttachment(file, null)).rejects.toThrow(
      "Attachments must be 25 MB or smaller."
    );
  });
});
