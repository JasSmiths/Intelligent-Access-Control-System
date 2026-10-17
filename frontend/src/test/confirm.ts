import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";

/** Answer the in-app confirmation (ui/useConfirm) and wait for it to close. */
export async function answerConfirm(choice: "confirm" | "cancel", name?: string | RegExp) {
  const prompt = await screen.findByRole("alertdialog", name === undefined ? undefined : { name });
  const buttons = within(prompt).getAllByRole("button");
  fireEvent.click(choice === "confirm" ? buttons[buttons.length - 1] : buttons[0]);
  await waitFor(() => expect(prompt).not.toBeInTheDocument());
}

export function expectNoConfirm() {
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
}
