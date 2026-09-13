

type ScheduleDependencyItem = {
  id: string;
  name: string;
  kind: string;
  entity_id?: string | null;
  registration_number?: string | null;
  owner?: string | null;
};

export type ScheduleDependencies = {
  people: ScheduleDependencyItem[];
  vehicles: ScheduleDependencyItem[];
  doors: ScheduleDependencyItem[];
};

import { api, createActionConfirmation, type ApiRequestOptions } from "./client";
import type { Schedule } from "./types";

export type SchedulePayload = Pick<Schedule, "name" | "description" | "time_blocks">;

export const schedulesApi = {
  dependencies(id: string, options: ApiRequestOptions = {}) {
    return api.get<ScheduleDependencies>(`/api/v1/schedules/${id}/dependencies`, options);
  },
  async save(payload: SchedulePayload, schedule?: Schedule | null): Promise<Schedule> {
    const confirmation = await createActionConfirmation(
      schedule ? "schedule.update" : "schedule.create",
      schedule ? { ...payload, schedule_id: schedule.id } : payload,
      { target_entity: "Schedule", ...(schedule ? { target_id: schedule.id } : {}), target_label: payload.name,
        reason: schedule ? "Update access schedule" : "Create access schedule" }
    );
    const body = { ...payload, confirmation_token: confirmation.confirmation_token };
    return schedule ? api.patch<Schedule>(`/api/v1/schedules/${schedule.id}`, body) : api.post<Schedule>("/api/v1/schedules", body);
  },
  async delete(schedule: Schedule): Promise<void> {
    const confirmation = await createActionConfirmation("schedule.delete", { schedule_id: schedule.id }, {
      target_entity: "Schedule", target_id: schedule.id, target_label: schedule.name, reason: "Delete access schedule"
    });
    await api.delete(`/api/v1/schedules/${schedule.id}`, { confirmation_token: confirmation.confirmation_token });
  }
};
