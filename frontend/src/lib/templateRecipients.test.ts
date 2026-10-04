import { expect, it } from "vitest";
import { renderRecipientTemplate, restrictionsAfterReplacement } from "./templateRecipients";

const rules = [{occurrence: 1, name: "VehicleTimeAway", target_ids: ["jason"]}];
it("matches the backend's per-occurrence rendering and punctuation cleanup", () => {
  const text = "@VehicleTimeAway, arrived @VehicleTimeAway.";
  const context = {VehicleTimeAway: "after 2hrs 20m"};
  expect(renderRecipientTemplate(text, context, rules, "jason")).toBe("after 2hrs 20m, arrived after 2hrs 20m.");
  expect(renderRecipientTemplate(text, context, rules, "steph")).toBe("after 2hrs 20m, arrived.");
});
it("moves the audience with its existing token when text or tokens are inserted", () => {
  const text = "@VehicleTimeAway then @VehicleTimeAway";
  expect(restrictionsAfterReplacement(text, rules, 0, 0, "🚙 @FirstName ")).toEqual([{...rules[0], occurrence: 2}]);
  const start = text.lastIndexOf("@");
  expect(restrictionsAfterReplacement(text, rules, start, text.length, "@FirstName ")).toEqual([]);
});

it("cleans saved variable insertion whitespace before punctuation in previews", () => {
  expect(renderRecipientTemplate("at the gate @VehicleTimeAway , I've let her in.", {VehicleTimeAway: "after 26m"})).toBe("at the gate after 26m, I've let her in.");
  expect(renderRecipientTemplate("@VehicleTimeAway \t; @VehicleTimeAway !", {VehicleTimeAway: "after 26m"})).toBe("after 26m; after 26m!");
});

it("preserves spaces in literal text and substituted values", () => {
  expect(renderRecipientTemplate("Literal , @VehicleTimeAway , next  word.", {VehicleTimeAway: "after 26m "})).toBe("Literal , after 26m , next  word.");
  expect(renderRecipientTemplate("@VehicleTimeAway\n, next.", {VehicleTimeAway: "after 26m"})).toBe("after 26m\n, next.");
});
