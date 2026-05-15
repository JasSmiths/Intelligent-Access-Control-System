import React from "react";
import { createPortal } from "react-dom";
import { useVirtualizer } from "@tanstack/react-virtual";
import { diff as jsonDiff } from "jsondiffpatch";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Bell,
  Bot,
  Camera,
  CalendarDays,
  Car,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock3,
  Command,
  ClipboardPaste,
  Construction,
  Copy,
  Database,
  DoorClosed,
  DoorOpen,
  Download,
  File as FileIcon,
  FileImage,
  FileText,
  Gauge,
  GitBranch,
  HardHat,
  Home,
  Key,
  LayoutDashboard,
  Lock,
  LogIn,
  LogOut,
  Loader2,
  MessageCircle,
  Menu,
  Moon,
  Monitor,
  MoreHorizontal,
  Play,
  PlugZap,
  Plus,
  Paperclip,
  Pencil,
  RefreshCcw,
  RefreshCw,
  Search,
  Send,
  Smile,
  Smartphone,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Save,
  Split,
  Sparkles,
  Sun,
  Terminal,
  Ticket,
  Trash2,
  Trophy,
  Type,
  Unlock,
  UserPlus,
  UserRound,
  Users,
  Volume2,
  Warehouse,
  X,
  Zap
} from "lucide-react";

import {
  AccessEvent,
  Badge,
  formatDate,
  matches,
  movementSagaDisplay,
  Toolbar,
  visitorEventDisplayName
} from "../shared";



export function EventSnapshotThumb({ event }: { event: AccessEvent }) {
  const label = `Snapshot for ${visitorEventDisplayName(event) || event.registration_number}`;
  if (!event.snapshot_url) {
    return (
      <span className="event-snapshot-placeholder" aria-hidden="true">
        <FileImage size={16} />
      </span>
    );
  }
  return (
    <span className="event-snapshot-thumb" tabIndex={0}>
      <img alt={label} loading="lazy" src={event.snapshot_url} />
      <span className="event-snapshot-preview" aria-hidden="true">
        <img alt="" loading="lazy" src={event.snapshot_url} />
      </span>
    </span>
  );
}

export function EventsView({ events, query }: { events: AccessEvent[]; query: string }) {
  const filtered = events.filter(
    (item) => matches(item.registration_number, query) || matches(item.source, query) || matches(item.visitor_name || "", query)
  );
  return (
    <section className="view-stack">
      <Toolbar title="Timeline" count={filtered.length} icon={Clock3} />
      <div className="table-card events-table-card">
        <table>
          <thead>
            <tr>
              <th>Snapshot</th>
              <th>Plate</th>
              <th>Direction</th>
              <th>Decision</th>
              <th>Movement</th>
              <th>Confidence</th>
              <th>When</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((event) => {
              const movement = movementSagaDisplay(event.movement_saga);
              return (
                <tr key={event.id}>
                  <td className="event-snapshot-cell">
                    <EventSnapshotThumb event={event} />
                  </td>
                  <td>
                    <strong>{event.registration_number}</strong>
                    {event.visitor_name ? <span className="table-muted-line">{visitorEventDisplayName(event)}</span> : null}
                  </td>
                  <td>{event.direction}</td>
                  <td><Badge tone={event.decision === "granted" ? "green" : "red"}>{event.decision}</Badge></td>
                  <td>{movement ? <Badge tone={movement.tone}>{movement.label}</Badge> : <span className="table-muted-line">--</span>}</td>
                  <td>{Math.round(event.confidence * 100)}%</td>
                  <td>{formatDate(event.occurred_at)}</td>
                  <td>{event.anomaly_count}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
