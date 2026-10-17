import "@testing-library/jest-dom/vitest";
import { act } from "@testing-library/react";
import { afterEach } from "vitest";
import { disposeConfirmHost } from "../ui/useConfirm";

afterEach(() => { act(() => disposeConfirmHost()); });
