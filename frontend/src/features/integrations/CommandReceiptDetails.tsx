import type { DeviceCommandReceipt, GateCommandReceipt } from "../../api/integrations";

type CommandReceipt = GateCommandReceipt | DeviceCommandReceipt;

export function CommandReceiptDetails({ receipt }: { receipt: CommandReceipt }) {
  const targets = "target_receipts" in receipt ? receipt.target_receipts : [receipt];
  return <div>
    {receipt.delivery === "partial" ? <p role="status">Partial command delivery. Results differ between targets; see each result below.</p> : null}
    {"admission_verified" in receipt && receipt.admission_verified ? <p>Entry admission verified. Vehicle passage is not established by this result.</p> : null}
    {!targets.length ? <p>No target result is available yet.</p> : null}
    {targets.map((target) => <div className="settings-list" key={target.command_id}>
      <strong>{target.device_key}</strong>
      <p>{target.delivery === "accepted" ? "Request accepted" : target.delivery === "rejected" ? "Request rejected" : target.delivery === "not_sent" ? "Request not sent" : "Delivery unknown"} · {target.verified ? `Physical ${target.state} verified` : "Physical state not verified"}</p>
      {target.provider_receipts?.map((provider, index) => <p key={`${provider.provider}:${index}`}>{provider.provider === "esphome" ? "ESPHome" : provider.provider === "home_assistant" ? "Home Assistant" : provider.provider}: {provider.acceptance_basis === "home_assistant_http_2xx" ? "service request accepted" : provider.acceptance_basis === "native_api_write" ? "controller SDK write completed" : provider.delivery}</p>)}
      {target.detail ? <p>{target.detail}</p> : null}
      {target.requires_reconciliation ? <p>Awaiting reconciliation. Do not repeat this command.</p> : null}
    </div>)}
  </div>;
}
