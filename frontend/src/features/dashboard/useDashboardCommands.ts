import React from "react";
import { ApiError } from "../../api/client";
import { integrationsApi, coverTargetReceipt, isDeviceCommandReceipt, isGateCommandReceipt, type DeviceCommandReceipt, type GateCommandReceipt } from "../../api/integrations";
import type { UserAccount } from "../../api/types";
import type { DashboardCommand, DoorCommandAction } from "./types";

type SavedDashboardCommand = { intentId: string; kind: DashboardCommand["kind"]; deviceKey?: string; action: DoorCommandAction };
type DashboardReceipt = GateCommandReceipt | DeviceCommandReceipt;
type ReceiptRead = { receipt?: DashboardReceipt; error?: string; loading?: boolean };

function loadSavedCommands(key: string): SavedDashboardCommand[] {
  try {
    const value: unknown = JSON.parse(window.sessionStorage.getItem(key) || "[]");
    if (!Array.isArray(value)) return [];
    return value.flatMap((item: unknown) => {
      if (!item || typeof item !== "object") return [];
      const row = item as Record<string, unknown>;
      if (typeof row.intentId !== "string" || !row.intentId || (row.kind !== "gate" && row.kind !== "garage_door")
        || (row.action !== "open" && row.action !== "close") || (row.deviceKey !== undefined && typeof row.deviceKey !== "string")) return [];
      return [{ intentId: row.intentId, kind: row.kind, action: row.action, ...(row.deviceKey ? { deviceKey: row.deviceKey } : {}) }];
    });
  } catch { return []; }
}

export function useDashboardCommands({ currentUser, pendingCommand, maintenanceActive, closeCommand, refresh }: {
  currentUser: UserAccount;
  pendingCommand: DashboardCommand | null;
  maintenanceActive: boolean;
  closeCommand: () => void | Promise<void>;
  refresh: () => Promise<void>;
}) {
  const isAdmin = currentUser.role === "admin";
  const [commandLoading, setCommandLoading] = React.useState(false);
  const [commandError, setCommandError] = React.useState("");
  const storageKey = `iacs-dashboard-commands:${currentUser.id}:${currentUser.role}`;
  const [savedCommands, setSavedCommands] = React.useState(() => loadSavedCommands(storageKey));
  const [receiptReads, setReceiptReads] = React.useState<Record<string, ReceiptRead>>({});
  const [receiptRefresh, setReceiptRefresh] = React.useState(0);
  const [receiptStorageError, setReceiptStorageError] = React.useState("");
  const commandAttemptRef = React.useRef(false);
  const commandAbortRef = React.useRef<AbortController | null>(null);
  const refreshTimerRef = React.useRef<number | null>(null);
  const lifetimeRef = React.useRef(0);
  React.useLayoutEffect(() => {
    lifetimeRef.current += 1;
    return () => {
      lifetimeRef.current += 1;
      commandAbortRef.current?.abort();
      if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
    };
  }, []);
  const persistCommands = (commands: SavedDashboardCommand[]) => {
    // Advisory browser index; the durable backend receipt owns the outcome.
    try { window.sessionStorage.setItem(storageKey, JSON.stringify(commands)); }
    catch { setReceiptStorageError("This browser cannot retain action IDs across reloads. Open Command History in Settings to find saved server receipts."); }
    setSavedCommands(commands);
  };
  React.useEffect(() => {
    if (!isAdmin || commandLoading || !savedCommands.length) return;
    const controller = new AbortController();
    for (const command of savedCommands) {
      setReceiptReads((reads) => ({ ...reads, [command.intentId]: { ...reads[command.intentId], loading: true } }));
      const request = command.kind === "gate"
        ? integrationsApi.getGateCommandByIntent(command.intentId, { signal: controller.signal })
        : integrationsApi.getCoverCommandByIntent(command.intentId, { signal: controller.signal });
      request.then((receipt) => {
        if (controller.signal.aborted) return;
        if (!(command.kind === "gate" ? isGateCommandReceipt(receipt) : isDeviceCommandReceipt(receipt))) {
          throw new Error("The saved action result was incomplete.");
        }
        setReceiptReads((reads) => ({ ...reads, [command.intentId]: { receipt } }));
      }).catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof ApiError && error.status === 404
          ? "No recorded attempt is available yet. The request may still be processing; this does not establish that it was not sent."
          : error instanceof Error ? error.message : "Unable to read the saved action result.";
        setReceiptReads((reads) => ({ ...reads, [command.intentId]: { receipt: reads[command.intentId]?.receipt, error: message } }));
      });
    }
    return () => controller.abort();
  }, [commandLoading, isAdmin, receiptRefresh, savedCommands]);

  const awaitingResult = (kind: DashboardCommand["kind"], deviceKey?: string) => savedCommands.some((command) => {
    if (command.kind !== kind || (command.deviceKey && deviceKey && command.deviceKey !== deviceKey)) return false;
    const receipt = receiptReads[command.intentId]?.receipt;
    return !receipt || receipt.requires_reconciliation;
  });

  const runDashboardCommand = async () => {
    if (!pendingCommand || commandAttemptRef.current || !isAdmin || maintenanceActive) return;
    const command = pendingCommand;
    commandAttemptRef.current = true;
    setCommandLoading(true);
    setCommandError("");
    const lifetime = lifetimeRef.current;
    const active = () => lifetimeRef.current === lifetime;
    const controller = new AbortController();
    commandAbortRef.current = controller;
    let saved: SavedDashboardCommand | null = null;
    try {
      const reason = `Dashboard ${command.label} ${command.action} command`;
      const gatePayload = { reason, ...(command.entity_id ? { target_device_key: command.entity_id } : {}) };
      const coverPayload = { entity_id: command.entity_id || "", action: command.action, reason };
      const confirmation = command.kind === "gate"
        ? await integrationsApi.confirmGateOpen(gatePayload, command.label)
        : await integrationsApi.confirmCoverCommand(coverPayload, command.label);
      if (!active()) return;
      if (!confirmation.confirmation_id) throw new Error("The action confirmation did not include a recovery ID. No command was requested.");
      saved = { intentId: confirmation.confirmation_id, kind: command.kind, action: command.action, ...(command.entity_id ? { deviceKey: command.entity_id } : {}) };
      persistCommands([...savedCommands.filter((item) => item.intentId !== saved!.intentId), saved]);
      const result = command.kind === "gate"
        ? await integrationsApi.openGate(gatePayload, confirmation.confirmation_token, { signal: controller.signal })
        : await integrationsApi.commandCover(coverPayload, confirmation.confirmation_token, { signal: controller.signal });
      if (!active()) return;
      const receipt = command.kind === "gate" && isGateCommandReceipt(result) ? result : coverTargetReceipt(result);
      setReceiptReads((reads) => ({ ...reads, [saved!.intentId]: receipt
        ? { receipt } : { error: "The response did not include a complete receipt. Check the saved action result before another command." } }));
    } catch (error) {
      if (!active()) return;
      if (saved) {
        const payload: unknown = error instanceof ApiError ? error.payload : null;
        const receipt = command.kind === "gate" && isGateCommandReceipt(payload) ? payload : coverTargetReceipt(payload);
        setReceiptReads((reads) => ({ ...reads, [saved!.intentId]: {
          ...(receipt ? { receipt } : {}),
          error: receipt ? undefined : "The command response was not received. Its delivery is unknown. Check the saved result; do not repeat the command."
        } }));
      } else {
        setCommandError(error instanceof Error ? error.message : "Unable to confirm the action.");
      }
    } finally {
      if (active()) {
        commandAttemptRef.current = false;
        commandAbortRef.current = null;
        setCommandLoading(false);
        if (saved) {
          void closeCommand();
          refresh().catch(() => undefined);
          if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
          refreshTimerRef.current = window.setTimeout(() => {
            refreshTimerRef.current = null;
            refresh().catch(() => undefined);
          }, 2500);
        }
      }
    }
  };

  return { commandLoading, commandError, setCommandError, savedCommands, receiptReads, setReceiptRefresh,
    receiptStorageError, persistCommands, awaitingResult, runDashboardCommand };
}
