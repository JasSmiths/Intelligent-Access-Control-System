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
