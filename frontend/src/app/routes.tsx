import { DoorOpen, Gauge, Lock, MapPinned, SlidersHorizontal, Warehouse } from "lucide-react";
import React from "react";
import { RouteErrorBoundary } from "../RouteErrorBoundary";
import type { AccessEvent, Anomaly, ExpectedPresenceSummary, Group, IntegrationStatus, MaintenanceStatus, NavigateToView, Person, Presence, RealtimeMessage, Schedule, UserAccount, Vehicle, ViewKey } from "../api/types";
const Dashboard = React.lazy(() => import("../views/DashboardView").then((module) => ({ default: module.Dashboard })));
const GroupsView = React.lazy(() => import("../views/DirectoryViews").then((module) => ({ default: module.GroupsView })));
const PeopleView = React.lazy(() => import("../views/DirectoryViews").then((module) => ({ default: module.PeopleView })));
const VehiclesView = React.lazy(() => import("../views/DirectoryViews").then((module) => ({ default: module.VehiclesView })));
const SchedulesView = React.lazy(() => import("../views/SchedulesView").then((module) => ({ default: module.SchedulesView })));
const PassesView = React.lazy(() => import("../views/PassesView").then((module) => ({ default: module.PassesView })));
const TopChartsView = React.lazy(() => import("../views/TopChartsView").then((module) => ({ default: module.TopChartsView })));
const EventsView = React.lazy(() => import("../views/EventsView").then((module) => ({ default: module.EventsView })));
const MovementsView = React.lazy(() => import("../views/MovementsView").then((module) => ({ default: module.MovementsView })));
const AlertsView = React.lazy(() => import("../views/AlertsView").then((module) => ({ default: module.AlertsView })));
const ReportsView = React.lazy(() => import("../views/ReportsView").then((module) => ({ default: module.ReportsView })));
const IntegrationsView = React.lazy(() => import("../views/IntegrationsView").then((module) => ({ default: module.IntegrationsView })));
const LogsView = React.lazy(() => import("../views/LogsView").then((module) => ({ default: module.LogsView })));
const AlfredTrainingView = React.lazy(() => import("../views/AlfredTrainingView").then((module) => ({ default: module.AlfredTrainingView })));
const AutomationsView = React.lazy(() => import("../views/WorkflowViews").then((module) => ({ default: module.AutomationsView })));
const NotificationsView = React.lazy(() => import("../views/WorkflowViews").then((module) => ({ default: module.NotificationsView })));
const SettingsView = React.lazy(() => import("../views/SettingsViews").then((module) => ({ default: module.SettingsView })));
const DynamicSettingsView = React.lazy(() => import("../views/SettingsViews").then((module) => ({ default: module.DynamicSettingsView })));
const AccessDevicesSettingsView = React.lazy(() => import("../views/SettingsViews").then((module) => ({ default: module.AccessDevicesSettingsView })));
const ZonesSettingsView = React.lazy(() => import("../views/SettingsViews").then((module) => ({ default: module.ZonesSettingsView })));
const UsersView = React.lazy(() => import("../views/SettingsViews").then((module) => ({ default: module.UsersView })));
function RouteLoading() {
  return <div className="loading-panel">Loading view</div>;
}export function View(props: {
  view: ViewKey;
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
      content = <SchedulesView schedules={props.schedules} query={props.search} refresh={props.refresh} />;
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
      content = <EventsView events={props.events} query={props.search} />;
      break;
    case "movements":
      content = <MovementsView query={props.search} refreshToken={props.dataRefreshToken} />;
      break;
    case "alerts":
      content = <AlertsView refreshDashboard={props.refresh} refreshToken={props.dataRefreshToken} />;
      break;
    case "reports":
      content = <ReportsView events={props.events} people={props.people} presence={props.presence} />;
      break;
    case "integrations":
      content = <IntegrationsView currentUser={props.currentUser} people={props.people} latestRealtime={props.latestRealtime} refreshToken={props.dataRefreshToken} status={props.integrationStatus} />;
      break;
    case "logs":
      content = <LogsView refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_general":
      content = <DynamicSettingsView category="general" title="General Settings" icon={SlidersHorizontal} currentUser={props.currentUser} maintenanceStatus={props.maintenanceStatus} onMaintenanceStatusChanged={props.onMaintenanceStatusChanged} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_gates":
      content = <AccessDevicesSettingsView kind="gate" title="Gates" icon={DoorOpen} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_garage_doors":
      content = <AccessDevicesSettingsView kind="garage_door" title="Garage Doors" icon={Warehouse} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_auth":
      content = <DynamicSettingsView category="auth" title="Auth & Security" icon={Lock} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} />;
      break;
    case "alfred_training":
      content = props.currentUser.role === "admin"
        ? <AlfredTrainingView refreshToken={props.dataRefreshToken} />
        : <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "settings_automations":
      content = <AutomationsView people={props.people} refreshToken={props.dataRefreshToken} vehicles={props.vehicles} />;
      break;
    case "settings_notifications":
      content = <NotificationsView currentUser={props.currentUser} people={props.people} refreshToken={props.dataRefreshToken} schedules={props.schedules} />;
      break;
    case "settings_lpr":
      content = <DynamicSettingsView category="lpr" title="LPR Tuning" icon={Gauge} currentUser={props.currentUser} refreshToken={props.dataRefreshToken} />;
      break;
    case "settings_zones":
      content = <ZonesSettingsView icon={MapPinned} refreshToken={props.dataRefreshToken} currentUser={props.currentUser} />;
      break;
    case "settings":
      content = <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
      break;
    case "users":
      content = props.currentUser.role === "admin"
        ? <UsersView currentUser={props.currentUser} onCurrentUserUpdated={props.onCurrentUserUpdated} refreshToken={props.dataRefreshToken} />
        : <SettingsView currentUser={props.currentUser} groups={props.groups} schedules={props.schedules} vehicles={props.vehicles} />;
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
