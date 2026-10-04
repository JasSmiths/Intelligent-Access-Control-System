import { History } from "lucide-react";
import type { UserAccount } from "../api/types";
import { CommandReceiptDetails } from "../features/integrations/CommandReceiptDetails";
import { CommandReceiptHistory } from "../features/integrations/CommandReceiptHistory";
import { Toolbar } from "../ui/primitives";

export function CommandHistoryView({ currentUser, targetId }: { currentUser: UserAccount; targetId?: string | null }) {
  if (currentUser.role !== "admin") {
    return <section className="view-stack"><div className="permission-state" role="alert">Administrator access required for Command History.</div></section>;
  }
  return (
    <section className="view-stack settings-page">
      <Toolbar title="Command History" icon={History} />
      <CommandReceiptHistory key={targetId} targetId={targetId} currentUser={currentUser} renderReceipt={(receipt) => <CommandReceiptDetails receipt={receipt} />} />
    </section>
  );
}
