"""Compliance, camera, and file Alfred tool handlers."""

from __future__ import annotations

from app.ai.tool_groups._facade_handlers import facade_handler

lookup_dvla_vehicle = facade_handler("lookup_dvla_vehicle")
analyze_camera_snapshot = facade_handler("analyze_camera_snapshot")
read_chat_attachment = facade_handler("read_chat_attachment")
export_presence_report_csv = facade_handler("export_presence_report_csv")
generate_contractor_invoice_pdf = facade_handler("generate_contractor_invoice_pdf")
get_camera_snapshot = facade_handler("get_camera_snapshot")
