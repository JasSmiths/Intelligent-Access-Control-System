import type { Person } from "../../api/types";

export type PersonPronouns = NonNullable<Person["pronouns"]>;

export type PersonPronounFormValue = PersonPronouns | "";

export type HomeAssistantInputBooleanAction = Person["home_assistant_presence_input_boolean_entry_action"];

export type HomeAssistantPersonSuggestion = {
  mobile?: {
    id: string;
    label: string;
    confidence: number;
  };
};

export type DvlaLookupResponse = {
  registration_number: string;
  vehicle: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
    fuelType?: string | null;
  } & Record<string, unknown>;
  display_vehicle?: {
    make?: string | null;
    model?: string | null;
    colour?: string | null;
    color?: string | null;
    fuelType?: string | null;
  } & Record<string, unknown>;
  normalized_vehicle?: {
    registration_number?: string | null;
    make?: string | null;
    colour?: string | null;
    color?: string | null;
    fuel_type?: string | null;
    mot_status?: string | null;
    mot_expiry?: string | null;
    tax_status?: string | null;
    tax_expiry?: string | null;
  };
};
