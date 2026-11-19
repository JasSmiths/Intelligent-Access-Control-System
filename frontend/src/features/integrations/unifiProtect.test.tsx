import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { UnifiProtectStatus } from "../../api/integrations";
import type { UnifiProtectCamera } from "../../api/types";
import { UnifiProtectCameraSection, UnifiProtectExposesPanel } from "./unifiProtect";

afterEach(cleanup);

const status: UnifiProtectStatus = {
  configured: true, connected: true, realtime_connected: true, realtime_error: null,
  last_error: null, camera_count: 1, host: "example.invalid", port: 443, verify_ssl: true,
  websocket_states: { private: "connected", events: "connected", devices: "connected" },
  snapshot_width: 320, snapshot_height: 180
};
const camera: UnifiProtectCamera = {
  id: "gate", name: "Gate", model: null, state: "connected",
  is_recording: false, is_recording_enabled: true, is_video_ready: true,
  is_motion_detected: null, is_smart_detected: false, last_motion_at: null,
  last_smart_detect_at: null, snapshot_url: "/fixture", smart_detect_zones: [],
  channels: [{ id: "0", name: "High", width: 1920, height: 1080, fps: null, bitrate: null,
    is_rtsp_enabled: null, is_package: false }],
  feature_flags: { has_mic: false, has_package_camera: null, has_smart_detect: true,
    smart_detect_types: [], smart_detect_audio_types: [] },
  detections: { active: [] }
};

it("distinguishes unavailable camera state from a clear or disabled device", () => {
  const props = { status, cameras: [camera], error: "", loading: false, onRefresh: async () => {} };
  const card = render(<UnifiProtectCameraSection {...props} loaded onLoad={props.onRefresh} refreshToken={0} />);
  expect(within(screen.getByRole("article")).getByText("Unknown")).toBeInTheDocument();
  card.rerender(<UnifiProtectCameraSection {...props} cameras={[{ ...camera, is_motion_detected: false }]}
    loaded onLoad={props.onRefresh} refreshToken={0} />);
  expect(within(screen.getByRole("article")).getByText("Clear")).toBeInTheDocument();
  card.unmount();

  render(<UnifiProtectExposesPanel {...props} />);
  for (const field of ["motion", "active detections", "package camera"]) {
    expect(screen.getByRole("row", { name: `Gate ${field} Unknown` })).toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: /Channels/ }));
  expect(screen.getByRole("row", { name: /Gate · High .*RTSP unknown/ })).toBeInTheDocument();
});
