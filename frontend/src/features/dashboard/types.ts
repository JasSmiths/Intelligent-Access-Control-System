export type DoorCommandAction = "open" | "close";

export type DashboardCommand = {
  kind: "gate" | "garage_door";
  entity_id?: string;
  label: string;
  action: DoorCommandAction;
};
