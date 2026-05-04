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
  CardHeader,
  MetricCard,
  Presence
} from "../shared";



export function RhythmChart({ events }: { events: AccessEvent[] }) {
  const buckets = ["Entry", "Exit", "Denied"];
  const values = [
    events.filter((event) => event.direction === "entry").length,
    events.filter((event) => event.direction === "exit").length,
    events.filter((event) => event.decision === "denied").length
  ];
  const max = Math.max(...values, 1);
  return (
    <div className="bar-chart">
      {buckets.map((bucket, index) => (
        <div className="bar-row" key={bucket}>
          <span>{bucket}</span>
          <div className="bar-track">
            <div className={`bar-fill fill-${index}`} style={{ width: `${(values[index] / max) * 100}%` }} />
          </div>
          <strong>{values[index]}</strong>
        </div>
      ))}
    </div>
  );
}

export function ReportsView({ events, presence }: { events: AccessEvent[]; presence: Presence[] }) {
  return (
    <section className="dashboard-grid reports-grid">
      <MetricCard icon={FileText} label="Audit Events" value={String(events.length)} detail="latest window" tone="blue" />
      <MetricCard icon={UserRound} label="On Site" value={String(presence.filter((item) => item.state === "present").length)} detail="current occupancy" tone="green" />
      <MetricCard icon={AlertTriangle} label="Denied" value={String(events.filter((item) => item.decision === "denied").length)} detail="access attempts" tone="amber" />
      <div className="card span-3">
        <CardHeader icon={BarChart3} title="Duration Audit" action={<Badge tone="gray">Live data</Badge>} />
        <RhythmChart events={events} />
      </div>
    </section>
  );
}
