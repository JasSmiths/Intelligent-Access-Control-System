import { LockKeyhole } from "lucide-react";
import React from "react";
import type { UserAccount } from "../api/types";
import { InvestigationsWorkspace } from "../features/investigations/InvestigationsWorkspace";

export function LogsView({
  currentUser,
  refreshToken
}: {
  currentUser: UserAccount;
  refreshToken: number;
}) {
  React.useEffect(() => {
    document.body.classList.add("investigations-route");
    return () => document.body.classList.remove("investigations-route");
  }, []);

  if (currentUser.role !== "admin") {
    return (
      <section className="view-stack investigations-page">
        <div className="investigation-permission-state" role="alert">
          <LockKeyhole aria-hidden="true" size={28} />
          <h1>Activity investigations require administrator access</h1>
          <p>This page can contain sensitive audit and device evidence. Ask an administrator if you need access.</p>
        </div>
      </section>
    );
  }
  return <InvestigationsWorkspace refreshToken={refreshToken} />;
}
