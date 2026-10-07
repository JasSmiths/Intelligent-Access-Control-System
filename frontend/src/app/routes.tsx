import { DoorOpen, Gauge, Lock, MapPinned, SlidersHorizontal, Warehouse } from "lucide-react";
import React from "react";
import { LoadingState } from "../ui/primitives";
import { RouteErrorBoundary } from "../RouteErrorBoundary";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, Group, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, RealtimeMessage, Schedule, UserAccount, Vehicle, ViewKey } from "../api/types";
const Dashboard = React.lazy(() => import("../views/DashboardView").then((module) => ({ default: module.Dashboard })));
const GroupsView = React.lazy(() => import("../views/GroupsView").then((module) => ({ default: module.GroupsView })));
const PeopleView = React.lazy(() => import("../views/PeopleView").then((module) => ({ default: module.PeopleView })));
const VehiclesView = React.lazy(() => import("../views/VehiclesView").then((module) => ({ default: module.VehiclesView })));
const SchedulesView = React.lazy(() => import("../features/schedules/SchedulesView").then((module) => ({ default: module.SchedulesView })));
const PassesView = React.lazy(() => import("../views/PassesView").then((module) => ({ default: module.PassesView })));
const TopChartsView = React.lazy(() => import("../views/TopChartsView").then((module) => ({ default: module.TopChartsView })));
const EventsView = React.lazy(() => import("../views/EventsView").then((module) => ({ default: module.EventsView })));
const MovementsView = React.lazy(() => import("../views/MovementsView").then((module) => ({ default: module.MovementsView })));
const AlertsView = React.lazy(() => import("../views/AlertsView").then((module) => ({ default: module.AlertsView })));
const ReportsView = React.lazy(() => import("../views/ReportsView").then((module) => ({ default: module.ReportsView })));
const IntegrationsView = React.lazy(() => import("../views/IntegrationsView").then((module) => ({ default: module.IntegrationsView })));
const LogsView = React.lazy(() => import("../views/LogsView").then((module) => ({ default: module.LogsView })));
const AutomationsView = React.lazy(() => import("../features/workflows/AutomationsView").then((module) => ({ default: module.AutomationsView })));
const NotificationsView = React.lazy(() => import("../features/workflows/NotificationsView").then((module) => ({ default: module.NotificationsView })));
const SettingsView = React.lazy(() => import("../views/SettingsView").then((module) => ({ default: module.SettingsView })));
const DynamicSettingsView = React.lazy(() => import("../views/DynamicSettingsView").then((module) => ({ default: module.DynamicSettingsView })));
const AccessDevicesSettingsView = React.lazy(() => import("../views/AccessDevicesSettingsView").then((module) => ({ default: module.AccessDevicesSettingsView })));
const ZonesSettingsView = React.lazy(() => import("../views/ZonesSettingsView").then((module) => ({ default: module.ZonesSettingsView })));
const UsersView = React.lazy(() => import("../views/UsersView").then((module) => ({ default: module.UsersView })));
const CommandHistoryView = React.lazy(() => import("../views/CommandHistoryView").then((module) => ({ default: module.CommandHistoryView })));
const MissedExitRecoveryView = React.lazy(() => import("../features/missedExitRecovery/MissedExitRecoveryView").then((module) => ({ default: module.MissedExitRecoveryView })));
function RouteLoading() {
  return <LoadingState label="Loading view" />;
}
export function View(props: {
  view: ViewKey;
  locationSearch: string;
  search: string;
  presence: Presence[];
  expectedPresence: ExpectedPresenceSummary | null;
  events: AccessEvent[];
  anomalies: Anomaly[];
  people: Person[];
  vehicles: Vehicle[];
  groups: Group[];
  schedules: Schedule[];
  integrationStatus: IntegrationStatus | null;
  maintenanceStatus: MaintenanceStatus | null;
  latestRealtime: RealtimeMessage | null;
  dataRefreshToken: number;
  historyResetToken: number;
  refresh: () => Promise<void>;
  currentUser: UserAccount;
  navigateToView: NavigateToView;
  onCurrentUserUpdated: (user: UserAccount) => void;
  onMaintenanceStatusChanged: (status: MaintenanceStatus) => void;
}) {
  let content: React.ReactNode;
  switch (props.view) {
    case "people":
      content = <PeopleView garageDoors={props.integrationStatus?.garage_door_entities ?? []} groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "groups":
      content = <GroupsView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} />;
      break;
    case "schedules":
      content = <SchedulesView schedules={props.schedules} query={props.search} refreshToken={props.dataRefreshToken} refresh={props.refresh} />;
      break;
    case "passes":
      content = <PassesView query={props.search} latestRealtime={props.latestRealtime} refreshToken={props.dataRefreshToken} />;
      break;
    case "vehicles":
      content = <VehiclesView groups={props.groups} people={props.people} query={props.search} refresh={props.refresh} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "top_charts":
      content = <TopChartsView query={props.search} latestRealtime={props.latestRealtime} refreshToken={props.dataRefreshToken} />;
      break;
    case "events":
      content = <EventsView refreshToken={props.dataRefreshToken} resetToken={props.historyResetToken} targetId={new URLSearchParams(props.locationSearch).get("event")} />;
      break;
    case "movements":
      content = <MovementsView refreshToken={props.dataRefreshToken} resetToken={props.historyResetToken} targetId={new URLSearchParams(props.locationSearch).get("movement")} />;
      break;
    case "alerts":
      content = <AlertsView refreshDashboard={props.refresh} refreshToken={props.dataRefreshToken} resetToken={props.historyResetToken} targetId={new URLSearchParams(props.locationSearch).get("alert")} />;
      break;
    case "reports":
      content = <ReportsView events={props.events} people={props.people} presence={props.presence} />;
      break;
    case "integrations":
      content = <IntegrationsView currentUser={props.currentUser} latestRealtime={props.latestRealtime} refreshToken={props.dataRefreshToken} status={props.integrationStatus} />;
      break;
    case "logs":
      content = <LogsView currentUser={props.currentUser} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_general":
      content = <DynamicSettingsView key={`${props.currentUser.id}:general`} category="general" title="General Settings" icon={SlidersHorizontal} currentUser={props.currentUser} maintenanceStatus={props.maintenanceStatus} onMaintenanceStatusChanged={props.onMaintenanceStatusChanged} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_gates":
      content = <AccessDevicesSettingsView kind="gate" title="Gates" icon={DoorOpen} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_garage_doors":
      content = <AccessDevicesSettingsView kind="garage_door" title="Garage Doors" icon={Warehouse} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_missed_exit_recovery":
      content = <MissedExitRecoveryView targetId={new URLSearchParams(props.locationSearch).get("attempt")} key={`${props.currentUser.id}:${props.currentUser.role}`} currentUser={props.currentUser} people={props.people} refreshToken={props.dataRefreshToken} refresh={props.refresh} />;
      break;
    case "settings_command_history":
      content = <CommandHistoryView currentUser={props.currentUser} targetId={new URLSearchParams(props.locationSearch).get("command")} />;
      break;
    case "settings_auth":
      content = <DynamicSettingsView key={`${props.currentUser.id}:auth`} category="auth" title="Auth & Security" icon={Lock} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_automations":
      content = <AutomationsView key={`${props.currentUser.id}:${props.currentUser.role}`} currentUser={props.currentUser} people={props.people} refreshToken={props.dataRefreshToken} vehicles={props.vehicles} />;
      break;
    case "settings_notifications":
      content = <NotificationsView currentUser={props.currentUser} people={props.people} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_lpr":
      content = <DynamicSettingsView key={`${props.currentUser.id}:lpr`} category="lpr" title="LPR Tuning" icon={Gauge} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_zones":
      content = <ZonesSettingsView icon={MapPinned} refreshToken={props.dataRefreshToken} currentUser={props.currentUser} />;
      break;
    case "settings":
      content = <SettingsView currentUser={props.currentUser} navigateToView={props.navigateToView} />;
      break;
    case "users":
      content = props.currentUser.role === "admin"
        ? <UsersView currentUser={props.currentUser} onCurrentUserUpdated={props.onCurrentUserUpdated} refreshToken={props.dataRefreshToken} />
        : <div className="permission-state" role="alert">Administrator access required for Users.</div>;
      break;
    default:
      content = <Dashboard {...props} currentUser={props.currentUser} navigateToView={props.navigateToView} />;
      break;
  }
  return (
    <RouteErrorBoundary view={props.view}>
      <React.Suspense fallback={<RouteLoading />}>{content}</React.Suspense>
    </RouteErrorBoundary>
  );
}
