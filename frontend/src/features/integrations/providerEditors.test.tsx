import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { UserAccount } from "../../api/types";
import { createReadSignals, ReadSignalsContext } from "../../lib/readSignals";
import { IntegrationModal, assertManagedEditorCoverage, managedEditorKeys } from "./IntegrationModal";
import { integrationDefinitions } from "./catalog";
import { IntegrationsView } from "../../views/IntegrationsView";
import { CameraAiProviderSettings } from "./providers/CameraAiProviderSettings";
import type { EditorCloseGuard } from "../../ui/EditorDialog";
import { answerConfirm, expectNoConfirm } from "../../test/confirm";

const admin = { id: "synthetic-admin", role: "admin" } as UserAccount;
const values = { home_assistant_url: "http://inert.local", unifi_protect_host: "inert-protect", openai_api_key: true, llm_provider: "openai", ollama_base_url: "http://inert-model" };
const definition = (key: string) => integrationDefinitions(null, values, null).find((item) => item.key === key)!;
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((yes) => { resolve = yes; }); return { promise, resolve }; }
const esphomeDevice = { id: "controller", name: "Inert controller", host: "inert.local", port: 6053, timeout_seconds: 30, enabled: true, encryption_key_configured: false };
function fixture() {
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/api/v1/settings") return Object.entries(values).map(([key, value]) => ({ key, value }));
    if (path.endsWith("apprise/urls")) return { urls: [] };
    if (path.endsWith("esphome/devices")) return { devices: [] };
    if (path.endsWith("unifi-protect/status")) return { configured: false, connected: false, camera_count: 0 };
    if (path.endsWith("home-assistant/entities")) return { media_player_entities: [], cover_entities: [] };
    return { configured: true, connected: true };
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (path) => path.endsWith("action-confirmations") ? { confirmation_token: "inert-token" } : { ok: true, message: "Inert connection verified" });
  const patch = vi.spyOn(api, "patch").mockResolvedValue({ saved: true, settings: [], changed_keys: [], activation: { state: "not_required", services: {} } });
  const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
  return { get, post, patch, remove };
}
function mount(key: string, callbacks: { onClose?: () => void; onChanged?: () => Promise<void>; onGuardChange?: (guard: EditorCloseGuard | null) => void } = {}) {
  const signals = createReadSignals("first");
  const props = { definition: definition(key), currentUser: admin, initialTab: "general" as const, values, loading: false, onClose: callbacks.onClose ?? vi.fn(), onChanged: callbacks.onChanged ?? vi.fn().mockResolvedValue(undefined), onGuardChange: callbacks.onGuardChange };
  const rendered = render(<ReadSignalsContext.Provider value={signals}><IntegrationModal {...props} /></ReadSignalsContext.Provider>);
  return { ...rendered, signals, props };
}
async function draft(key: string) {
  if (key === "apprise") { const add = await screen.findByRole("button", { name: "Add New Apprise URL" }); await waitFor(() => expect(add).toBeEnabled()); fireEvent.click(add); fireEvent.change(screen.getByLabelText(/^Apprise URL/), { target: { value: "inert://retained-draft" } }); return screen.getByLabelText(/^Apprise URL/); }
  if (key === "esphome") { const add = await screen.findByRole("button", { name: "Add New ESPHome Device" }); await waitFor(() => expect(add).toBeEnabled()); fireEvent.click(add); fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Retained controller" } }); fireEvent.change(screen.getByLabelText("Host or IP"), { target: { value: "inert.local" } }); return screen.getByLabelText("Name"); }
  const label = key === "home_assistant" ? "URL" : key === "unifi_protect" ? "Console host" : "API key";
  const field = screen.getByLabelText(key === "openai" ? /^API key/ : label); fireEvent.change(field, { target: { value: "retained-draft" } }); return field;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("retains the accepted settings receipt and draft when provider activation fails", async () => {
  const { patch } = fixture();
  patch.mockResolvedValueOnce({ saved: true, settings: [], changed_keys: ["home_assistant_url"],
    activation: { state: "failed", services: { home_assistant: "failed" } } });
  const close = vi.fn();
  mount("home_assistant", { onClose: close });
  fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://saved.example.test" } });
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await screen.findByText(/Settings saved. Provider activation is failed/);
  expect(screen.getByLabelText("URL")).toHaveValue("https://saved.example.test");
  expect(close).not.toHaveBeenCalled();
  expect(screen.queryByText("Unable to save settings")).not.toBeInTheDocument();
});
it.each(["home_assistant", "unifi_protect", "openai", "apprise", "esphome"])("keeps the complete %s draft through settings refresh and guards every close route", async (key) => {
  fixture(); const close = vi.fn(); const canLeave = { current: null as EditorCloseGuard | null };
  const mounted = mount(key, { onClose: close, onGuardChange: (guard) => { canLeave.current = guard; } });
  const field = await draft(key); const retained = (field as HTMLInputElement).value;
  mounted.rerender(<ReadSignalsContext.Provider value={mounted.signals}><IntegrationModal {...mounted.props} values={{ ...values, home_assistant_url: "http://refreshed.local", unifi_protect_host: "refreshed" }} /></ReadSignalsContext.Provider>);
  expect(field).toHaveValue(retained);
  const dialog = screen.getByRole("dialog");
  const done = within(dialog).queryByRole("button", { name: "Done" });
  const routes = [() => fireEvent.click(within(dialog).getByRole("button", { name: "Close" })), () => fireEvent.keyDown(document, { key: "Escape" }), () => fireEvent.mouseDown(dialog.parentElement!), ...(done ? [() => fireEvent.click(done)] : [])];
  for (const route of routes) { route(); await answerConfirm("cancel", /^Discard unsaved/); }
  let decision: ReturnType<EditorCloseGuard> | undefined; act(() => { decision = canLeave.current?.(); });
  expect(decision).toBeInstanceOf(Promise); await answerConfirm("cancel"); await expect(decision).resolves.toBe(false);
  expect(close).not.toHaveBeenCalled(); expect(field).toHaveValue(retained);
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" })); await answerConfirm("confirm", /^Discard unsaved/); await waitFor(() => expect(close).toHaveBeenCalledOnce());
});
it("retains a Protect draft across tab and initialTab changes, resetting only for a new account", async () => {
  fixture(); const mounted = mount("unifi_protect"); await draft("unifi_protect");
  fireEvent.click(screen.getByRole("tab", { name: "Exposes" }));
  mounted.rerender(<ReadSignalsContext.Provider value={mounted.signals}><IntegrationModal {...mounted.props} initialTab="general" values={{ ...values, unifi_protect_host: "server-refresh" }} /></ReadSignalsContext.Provider>);
  expect(screen.getByRole("tab", { name: "Exposes" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("tab", { name: "General" })); expect(screen.getByLabelText("Console host")).toHaveValue("retained-draft");
  const next = createReadSignals("second"); mounted.rerender(<ReadSignalsContext.Provider value={next}><IntegrationModal {...mounted.props} values={{ ...values, unifi_protect_host: "new-account" }} /></ReadSignalsContext.Provider>);
  expect(screen.getByLabelText("Console host")).toHaveValue("new-account");
});
it("intercepts provider switching before the dirty keyed editor is replaced", async () => {
  fixture();
  render(<ReadSignalsContext.Provider value={createReadSignals("first")}><IntegrationsView currentUser={admin} /></ReadSignalsContext.Provider>);
  const apprise = screen.getByRole("button", { name: /^Apprise/ }); await waitFor(() => expect(apprise).toBeEnabled()); fireEvent.click(apprise); await draft("apprise");
  fireEvent.click(screen.getByRole("button", { name: /^ESPHome/ })); await answerConfirm("cancel", "Discard unsaved integration changes?");
  expect(screen.getByRole("dialog", { name: "Apprise" })).toBeInTheDocument(); expect(screen.getByLabelText(/^Apprise URL/)).toHaveValue("inert://retained-draft");
  fireEvent.click(screen.getByRole("button", { name: /^ESPHome/ })); await answerConfirm("confirm", "Discard unsaved integration changes?"); await screen.findByRole("dialog", { name: "ESPHome" }); expect(screen.queryByLabelText(/^Apprise URL/)).not.toBeInTheDocument();
});
it.each(["apprise", "esphome", "openai"])("blocks dismissal and duplicate writes for %s, then stops before writing after account replacement", async (key) => {
  const { post, patch } = fixture(); const confirmation = deferred<{ confirmation_token: string }>();
  post.mockReturnValueOnce(confirmation.promise); const close = vi.fn();
  const mounted = mount(key, { onClose: close }); const field = await draft(key);
  const action = key === "apprise" ? "Add URL" : key === "esphome" ? "Add Device" : "Save";
  const button = screen.getByRole("button", { name: action }); fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(field).toBeDisabled()); const retained = (field as HTMLInputElement).value;
  fireEvent.change(field, { target: { value: "must-not-replace" } }); expect(field).toHaveValue(retained);
  fireEvent.click(screen.getByRole("button", { name: "Close" })); fireEvent.keyDown(document, { key: "Escape" }); fireEvent.mouseDown(screen.getByRole("dialog").parentElement!);
  expect(close).not.toHaveBeenCalled(); expectNoConfirm(); expect(post).toHaveBeenCalledOnce(); expect(patch).not.toHaveBeenCalled();
  mounted.rerender(<ReadSignalsContext.Provider value={createReadSignals("second")}><IntegrationModal {...mounted.props} definition={definition("openai")} /></ReadSignalsContext.Provider>);
  await act(async () => confirmation.resolve({ confirmation_token: "old-account" }));
  expect(post).toHaveBeenCalledOnce(); expect(patch).not.toHaveBeenCalled(); expect(screen.getByLabelText(/^API key/)).toHaveValue(""); expect(screen.queryByText(/added|Settings saved/)).not.toBeInTheDocument();
});

it.each(["apprise", "esphome"])("retains accepted %s writes when local metadata and page reads fail", async (key) => {
  const { get, post } = fixture(); const mounted = mount(key, { onChanged: vi.fn().mockRejectedValue(new Error("Page metadata offline")) });
  await draft(key);
  post.mockImplementation(async (path) => {
    if (path.endsWith("action-confirmations")) return { confirmation_token: "inert-token" };
    get.mockRejectedValue(new Error("Provider metadata offline"));
    if (key === "apprise") return { urls: [{ index: 0, destination_id: "d".repeat(64), revision: "e".repeat(64), type: "Inert", scheme: "inert", preview: "Safe preview" }] };
    if (key === "esphome") return { devices: [esphomeDevice], activation: { state: "activated", services: { access_devices: "activated" } } };
    throw new Error("Unexpected provider");
  });
  fireEvent.click(screen.getByRole("button", { name: key === "apprise" ? "Add URL" : "Add Device" }));
  await screen.findByText(key === "apprise" ? "Apprise URL added" : "ESPHome device added. Waiting for native stream...");
  await screen.findAllByText("Provider metadata offline"); expect(screen.queryByText(/update failed|Unable to connect/)).not.toBeInTheDocument();
  expect(mounted.props.onChanged).toHaveBeenCalledOnce();
});
it("fences the delayed ESPHome stream read result after account disposal, even when transport ignores abort", async () => {
  const { get } = fixture(); const stream = deferred<unknown>(); let streamReads = 0;
  get.mockImplementation(async (path) => {
    if (path.endsWith("esphome/devices")) return { devices: [esphomeDevice] };
    if (path.endsWith("gate/status")) { streamReads += 1; if (streamReads === 2) return stream.promise; return { state_stream_status: { esphome: { running: false, devices: [] } } }; }
    return {};
  });
  const mounted = mount("esphome"); await waitFor(() => expect(streamReads).toBe(2), { timeout: 2000 });
  const oldSignal = get.mock.calls.filter(([path]) => path.endsWith("gate/status"))[1][1]?.signal;
  mounted.rerender(<ReadSignalsContext.Provider value={createReadSignals("second")}><IntegrationModal {...mounted.props} /></ReadSignalsContext.Provider>);
  expect(oldSignal?.aborted).toBe(true);
  await act(async () => stream.resolve({ state_stream_status: { esphome: { running: false, last_error: "Stale native stream failure", devices: [] } } }));
  expect(screen.queryByText("Stale native stream failure")).not.toBeInTheDocument();
});
it("stops a camera AI preference before write when its account is replaced during confirmation", async () => {
  const { post, patch } = fixture(); const confirmation = deferred<{ confirmation_token: string }>(); post.mockReturnValueOnce(confirmation.promise);
  const changed = vi.fn().mockResolvedValue(undefined);
  const mounted = render(<ReadSignalsContext.Provider value={createReadSignals("first")}><CameraAiProviderSettings currentUser={admin} values={values} loading={false} onChanged={changed} /></ReadSignalsContext.Provider>);
  fireEvent.change(screen.getByLabelText("Camera AI provider"), { target: { value: "ollama" } }); expect(screen.getByLabelText("Camera AI provider")).toBeDisabled();
  mounted.rerender(<ReadSignalsContext.Provider value={createReadSignals("second")}><CameraAiProviderSettings currentUser={admin} values={values} loading={false} onChanged={changed} /></ReadSignalsContext.Provider>);
  await act(async () => confirmation.resolve({ confirmation_token: "old-account" }));
  expect(patch).not.toHaveBeenCalled(); expect(changed).not.toHaveBeenCalled(); expect(screen.getByLabelText("Camera AI provider")).toBeEnabled();
});

it("requires exact catalog-derived managed editor coverage, rejecting missing and extra registrations", () => {
  const definitions = integrationDefinitions(null, {}, null);
  expect(() => assertManagedEditorCoverage(definitions, managedEditorKeys)).not.toThrow();
  for (const key of managedEditorKeys) expect(() => assertManagedEditorCoverage(definitions, managedEditorKeys.filter((candidate) => candidate !== key))).toThrow(`missing [${key}]`);
  expect(() => assertManagedEditorCoverage(definitions, [...managedEditorKeys, "unregistered_inert_provider"])).toThrow("extra [unregistered_inert_provider]");
  const scalar = definitions.find((item) => item.editor === "scalar")!;
  expect(() => assertManagedEditorCoverage(definitions, [...managedEditorKeys, scalar.key])).toThrow(`extra [${scalar.key}]`);
});

it("observes an accepted provider write after disposal without publishing it into the replacement editor", async () => {
  const { post } = fixture(); const accepted = deferred<unknown>();
  const changed = vi.fn().mockResolvedValue(undefined); const mounted = mount("apprise", { onChanged: changed });
  await draft("apprise");
  post.mockImplementation((path) => path.endsWith("action-confirmations") ? Promise.resolve({ confirmation_token: "inert-token" }) : accepted.promise);
  fireEvent.click(screen.getByRole("button", { name: "Add URL" })); await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  mounted.rerender(<ReadSignalsContext.Provider value={createReadSignals("second")}><IntegrationModal {...mounted.props} definition={definition("openai")} /></ReadSignalsContext.Provider>);
  await act(async () => accepted.resolve({ urls: [{ index: 0, destination_id: "d".repeat(64), revision: "e".repeat(64), type: "Inert", scheme: "inert", preview: "Accepted old URL" }] }));
  expect(changed).not.toHaveBeenCalled(); expect(screen.getByLabelText(/^API key/)).toHaveValue(""); expect(screen.queryByText("Apprise URL added")).not.toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(2);
});

it("shows a read-only copy field instead of a native prompt when the clipboard rejects the LPR token", async () => {
  fixture(); const logs = (["log", "info", "warn", "error", "debug"] as const).map((level) => vi.spyOn(console, level));
  const writeText = vi.fn().mockRejectedValueOnce(new Error("Clipboard denied")).mockResolvedValueOnce(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  try {
    mount("unifi_protect");
    const generate = (await screen.findByText("Generate")).closest("button")!; // Field actions sit inside the field label. await waitFor(() => expect(generate).toBeEnabled());
    fireEvent.click(generate);
    fireEvent.click(screen.getByText("Copy").closest("button")!);
    const field = await screen.findByRole("textbox", { name: "LPR webhook token (copy manually)" });
    const token = writeText.mock.calls[0][0] as string;
    expect(token).toMatch(/^[0-9a-f]{64}$/); expect(field).toHaveValue(token); expect(field).toHaveAttribute("readonly");
    expect(screen.getByText("Copy unavailable")).toBeInTheDocument(); expectNoConfirm();
    expect(logs.flatMap((spy) => spy.mock.calls).flat().some((value) => String(value).includes(token))).toBe(false);
    fireEvent.click(screen.getByText("Copy").closest("button")!);
    await screen.findByText("Token copied"); expect(screen.queryByRole("textbox", { name: "LPR webhook token (copy manually)" })).not.toBeInTheDocument();
  } finally { Reflect.deleteProperty(navigator, "clipboard"); }
});
