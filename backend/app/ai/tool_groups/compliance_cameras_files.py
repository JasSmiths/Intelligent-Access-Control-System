"""Alfred tool catalog entries for this domain."""

from __future__ import annotations

from app.ai.tools import AgentTool
from app.ai.tool_groups.compliance_cameras_files_handlers import (
    analyze_camera_snapshot,
    export_presence_report_csv,
    generate_contractor_invoice_pdf,
    get_camera_snapshot,
    lookup_dvla_vehicle,
    read_chat_attachment,
)
from app.ai.tool_groups.metadata import apply_group_metadata


TOOL_CATEGORIES = {
    "lookup_dvla_vehicle": ("Compliance_DVLA",),
    "analyze_camera_snapshot": ("Cameras", "Access_Diagnostics"),
    "read_chat_attachment": ("Reports_Files", "General"),
    "export_presence_report_csv": ("Reports_Files", "Access_Logs"),
    "generate_contractor_invoice_pdf": ("Reports_Files", "Access_Logs"),
    "get_camera_snapshot": ("Cameras",),
}


def build_tools() -> list[AgentTool]:
    return apply_group_metadata(
        [
        AgentTool(
                    name="lookup_dvla_vehicle",
                    description="Look up UK vehicle details from the DVLA Vehicle Enquiry Service by registration number.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "registration_number": {
                                "type": "string",
                                "description": "Vehicle registration number without spaces or punctuation.",
                            },
                        },
                        "required": ["registration_number"],
                        "additionalProperties": False,
                    },
                    handler=lookup_dvla_vehicle,
                    example_inputs=(
                        {"registration_number": "PE70DHX"},
                    ),
                    return_schema={
                        "answer_types": ["vehicle_compliance"],
                        "result_keys": ["registration_number", "display_vehicle", "tax_status", "mot_status"],
                    },
                ),
        AgentTool(
                    name="analyze_camera_snapshot",
                    description="Fetch a current UniFi Protect camera snapshot and ask the active vision-capable AI provider to analyze it.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"},
                            "camera_name": {"type": "string"},
                            "prompt": {
                                "type": "string",
                                "description": "What to inspect in the snapshot.",
                            },
                            "provider": {"type": "string"},
                        },
                        "required": ["prompt"],
                        "additionalProperties": False,
                    },
                    handler=analyze_camera_snapshot,
                    example_inputs=(
                        {"camera_name": "Gate", "prompt": "Is there a vehicle at the gate?"},
                    ),
                    return_schema={
                        "answer_types": ["camera_observation"],
                        "result_keys": ["camera", "analysis", "error"],
                    },
                ),
        AgentTool(
                    name="read_chat_attachment",
                    description="Read text from a user-uploaded chat document, or analyze an uploaded image with the active vision-capable provider.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "file_id": {"type": "string", "description": "Chat attachment file ID returned by upload."},
                            "prompt": {
                                "type": "string",
                                "description": "Question or extraction goal for the attachment.",
                            },
                            "provider": {"type": "string"},
                        },
                        "required": ["file_id"],
                        "additionalProperties": False,
                    },
                    handler=read_chat_attachment,
                ),
        AgentTool(
                    name="export_presence_report_csv",
                    description="Generate a CSV export of current presence and recent access events, then return a secure chat download link.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "person": {"type": "string"},
                            "group": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    handler=export_presence_report_csv,
                ),
        AgentTool(
                    name="generate_contractor_invoice_pdf",
                    description="Generate a simple PDF contractor visit invoice from calculated visit duration and return a secure chat download link.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "contractor_name": {"type": "string"},
                            "day": {"type": "string", "enum": ["today", "yesterday", "recent"]},
                            "hourly_rate": {"type": "number", "minimum": 0},
                            "currency": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    handler=generate_contractor_invoice_pdf,
                ),
        AgentTool(
                    name="get_camera_snapshot",
                    description="Fetch a current UniFi Protect camera snapshot and return it as a secure chat image attachment.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "camera_id": {"type": "string"},
                            "camera_name": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    handler=get_camera_snapshot,
                ),
        ],
        categories=TOOL_CATEGORIES,
    )
