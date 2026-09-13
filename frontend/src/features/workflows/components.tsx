import { ArrowLeft, Bell, ChevronDown, ChevronRight, Copy, GitBranch, MoreHorizontal, Pencil, Search, Trash2, X } from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";
import type { BadgeTone } from "../../ui/primitives";
import { Badge } from "../../ui/primitives";
import type { NotificationFilterCounts, NotificationStatusFilter, TwoPaneCategory, WorkflowListCategory, WorkflowRuleBase, WorkflowRuleListKind, WorkflowRuleMenuState, WorkflowRuleStatusFeedback } from "./model";
import { formatCompactLastFired, pluralize } from "./model";

const workflowStatusFilterOptions: Array<{ key: NotificationStatusFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "inactive", label: "Inactive" },
];

function workflowRuleStatusFeedbackLabel(status: WorkflowRuleStatusFeedback["status"]) {
  if (status === "paused") return "Paused";
  if (status === "saved") return "Saved";
  return "Resumed";
}

export function WorkflowRuleList<Rule extends WorkflowRuleBase>({
  activeId, ariaLabel, groupedRules, kind, renderConfigChips, ruleStatusFeedback, statusFilter, summaryAriaLabel, tableIdPrefix, totalRuleCount, togglingRuleIds, onDelete, onDuplicate, onSelect, onToggleActive
}: {
  activeId: string; ariaLabel: string; groupedRules: WorkflowListCategory<Rule>[]; kind: WorkflowRuleListKind; renderConfigChips: (rule: Rule) => React.ReactNode; ruleStatusFeedback: WorkflowRuleStatusFeedback | null; statusFilter: NotificationStatusFilter; summaryAriaLabel: string; tableIdPrefix: string; totalRuleCount: number; togglingRuleIds: Set<string>; onDelete: (rule: Rule) => void | Promise<void>; onDuplicate?: (rule: Rule) => void | Promise<void>; onSelect: (rule: Rule) => void; onToggleActive: (rule: Rule, isActive: boolean) => void | Promise<void>;
}) {
  const [openMenu, setOpenMenu] = React.useState<WorkflowRuleMenuState | null>(null);
  const [collapsedCategoryIds, setCollapsedCategoryIds] = React.useState<Set<string>>(() => new Set());

  React.useEffect(() => {
    if (!openMenu) return undefined;
    const closeOnPointerDown = (event: PointerEvent) => {
      if ((event.target as HTMLElement | null)?.closest("[data-workflow-rule-menu]")) return;
      setOpenMenu(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenMenu(null);
    };
    const closeOnViewportChange = () => setOpenMenu(null);
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openMenu]);

  React.useEffect(() => {
    setCollapsedCategoryIds(new Set());
    setOpenMenu(null);
  }, [statusFilter]);

  const toggleCategory = (categoryId: string) => {
    setCollapsedCategoryIds((current) => {
      const next = new Set(current);
      if (next.has(categoryId)) next.delete(categoryId);
      else next.add(categoryId);
      return next;
    });
  };
  const toggleRuleMenu = (ruleId: string, button: HTMLButtonElement) => {
    setOpenMenu((current) => {
      if (current?.id === ruleId) return null;
      const rect = button.getBoundingClientRect();
      const menuWidth = 178;
      const menuHeight = onDuplicate ? 136 : 94;
      const gap = 7;
      const left = Math.max(12, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 12));
      const below = rect.bottom + gap;
      const top = below + menuHeight > window.innerHeight - 12 ? Math.max(12, rect.top - menuHeight - gap) : below;
      return { id: ruleId, left, top };
    });
  };

  return (
    <aside className="workflow-rule-table notification-workflow-table card" aria-label={ariaLabel}>
      {groupedRules.length ? (
        <div className="notification-category-stack">
          {groupedRules.map((category) => {
            const Icon = category.icon;
            const collapsed = collapsedCategoryIds.has(category.id);
            const tableId = `${tableIdPrefix}-${category.id}`;
            return (
              <section className="notification-category-folder" key={category.id}>
                <button aria-controls={tableId} aria-expanded={!collapsed} className="notification-category-header" onClick={() => toggleCategory(category.id)} type="button">
                  {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
                  <Icon size={16} />
                  <span><strong>{category.label}</strong></span>
                  <Badge tone="gray">{category.rules.length}</Badge>
                </button>
                {!collapsed ? (
                  <div className="notification-rule-table-wrap" id={tableId}>
                    <table className="notification-rule-data-table">
                      <thead><tr><th>Name</th><th>Configuration</th><th>Last Fired</th><th>Actions</th></tr></thead>
                      <tbody>
                        {category.rules.map((rule) => {
                          const menuOpen = openMenu?.id === rule.id;
                          const statusFeedback = ruleStatusFeedback?.ruleId === rule.id ? ruleStatusFeedback : null;
                          return (
                            <tr className={[activeId === rule.id ? "active" : "", rule.is_active ? "" : "paused"].filter(Boolean).join(" ")} key={rule.id}>
                              <td className="notification-rule-name-cell"><button className="notification-rule-name-button" onClick={() => onSelect(rule)} type="button"><strong>{rule.name}</strong></button></td>
                              <td><span className="notification-config-chips" aria-label={summaryAriaLabel}>{renderConfigChips(rule)}</span></td>
                              <td><span className="notification-last-fired">{formatCompactLastFired(rule.last_fired_at)}</span></td>
                              <td className="notification-rule-actions-cell">
                                <span className="notification-rule-actions-cluster">
                                  <span className="notification-rule-status-pill-slot">{statusFeedback ? <span className={`notification-rule-status-pill ${statusFeedback.status}`} key={statusFeedback.nonce} role="status">{workflowRuleStatusFeedbackLabel(statusFeedback.status)}</span> : null}</span>
                                  <label className={rule.is_active ? "workflow-rule-toggle active" : "workflow-rule-toggle"} aria-label={`${rule.is_active ? "Pause" : "Activate"} ${rule.name}`}>
                                    <input checked={rule.is_active} disabled={togglingRuleIds.has(rule.id)} onChange={(event) => onToggleActive(rule, event.target.checked)} type="checkbox" />
                                    <span className="workflow-rule-toggle-track" aria-hidden="true"><span /></span>
                                  </label>
                                  <span className="workflow-rule-menu" data-workflow-rule-menu>
                                    <button aria-expanded={menuOpen} aria-haspopup="menu" aria-label={`Options for ${rule.name}`} className="icon-button workflow-rule-menu-button" onClick={(event) => toggleRuleMenu(rule.id, event.currentTarget)} type="button"><MoreHorizontal size={16} /></button>
                                  </span>
                                </span>
                                {menuOpen ? <WorkflowRuleMenu left={openMenu.left} rule={rule} top={openMenu.top} onClose={() => setOpenMenu(null)} onDelete={onDelete} onDuplicate={onDuplicate} onSelect={onSelect} /> : null}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      ) : <WorkflowEmptyState kind={kind} statusFilter={statusFilter} totalRuleCount={totalRuleCount} />}
    </aside>
  );
}

function WorkflowRuleMenu<Rule extends WorkflowRuleBase>({ left, rule, top, onClose, onDelete, onDuplicate, onSelect }: { left: number; rule: Rule; top: number; onClose: () => void; onDelete: (rule: Rule) => void | Promise<void>; onDuplicate?: (rule: Rule) => void | Promise<void>; onSelect: (rule: Rule) => void }) {
  return createPortal(
    <div className="workflow-rule-menu-popover" data-workflow-rule-menu role="menu" style={{ left, top }}>
      <button onClick={() => { onClose(); onSelect(rule); }} role="menuitem" type="button"><Pencil size={14} /> Edit</button>
      {onDuplicate ? <button onClick={() => { onClose(); onDuplicate(rule); }} role="menuitem" type="button"><Copy size={14} /> Duplicate</button> : null}
      <button className="danger" onClick={() => { onClose(); onDelete(rule); }} role="menuitem" type="button"><Trash2 size={14} /> Delete</button>
    </div>,
    document.body
  );
}

function WorkflowEmptyState({ kind, statusFilter, totalRuleCount }: { kind: WorkflowRuleListKind; statusFilter: NotificationStatusFilter; totalRuleCount: number }) {
  const automation = kind === "automation";
  const Icon = automation ? GitBranch : Bell;
  const emptyTitle = totalRuleCount === 0 ? (automation ? "No automation rules" : "No notification workflows") : statusFilter === "active" ? (automation ? "No active automation rules" : "No active notification workflows") : (automation ? "No paused automation rules" : "No inactive notification workflows");
  const emptyDetail = totalRuleCount === 0 ? (automation ? "Use Add Automation to create the first Trigger / If / Then rule." : "Use Add Notification to create the first automation.") : statusFilter === "active" ? (automation ? "Active automation rules will appear here as soon as they are switched on." : "Active workflows will appear here as soon as they are switched on.") : (automation ? "Paused automation rules will appear here as soon as they are switched off." : "Paused workflows will appear here as soon as they are switched off.");
  return <div className="notification-empty-list workflow-empty-list"><Icon size={20} /><strong>{emptyTitle}</strong><span>{emptyDetail}</span></div>;
}

export function WorkflowStatusFilters({
  activeFilter,
  ariaLabel,
  counts,
  onFilterChange
}: {
  activeFilter: NotificationStatusFilter;
  ariaLabel: string;
  counts: NotificationFilterCounts;
  onFilterChange: (filter: NotificationStatusFilter) => void;
}) {
  return (
    <div className="notification-status-tabs" role="tablist" aria-label={ariaLabel}>
      {workflowStatusFilterOptions.map((option) => (
        <button
          aria-selected={activeFilter === option.key}
          className={activeFilter === option.key ? "active" : ""}
          key={option.key}
          onClick={() => onFilterChange(option.key)}
          role="tab"
          type="button"
        >
          <span>{option.label}</span>
          <Badge tone="gray">{counts[option.key]}</Badge>
        </button>
      ))}
    </div>
  );
}

export function NotificationConfigChip({ count, icon: Icon, label }: { count: number; icon: React.ElementType; label: string }) {
  const itemName = label === "Triggers" ? "trigger" : label === "Conditions" ? "condition" : "action";
  const tooltip = `${count} ${pluralize(itemName, count)} configured`;
  return (
    <span
      className="notification-config-chip"
      aria-label={`${label}: ${count}`}
      title={tooltip}
    >
      <Icon size={13} />
      <span>{count}</span>
    </span>
  );
}

export function WorkflowBlock({
  badge,
  children,
  optional,
  required,
  title,
  tone
}: {
  badge: string;
  children: React.ReactNode;
  optional?: boolean;
  required?: boolean;
  title: string;
  tone: BadgeTone;
}) {
  return (
    <section className="workflow-block">
      <div className="workflow-block-head">
        <Badge tone={tone}>{badge}</Badge>
        <strong>{title}</strong>
        <span>{required ? "Required" : optional ? "Optional" : ""}</span>
      </div>
      {children}
    </section>
  );
}

export function TwoPaneSelectionModal({
  activeCategoryId,
  backLabel = "Back to editor",
  categories,
  children,
  embedded = false,
  footer,
  onBack,
  onCategoryChange,
  onClose,
  onSearchChange,
  searchPlaceholder = "Search",
  searchQuery,
  subtitle,
  title,
  wide = false
}: {
  activeCategoryId: string;
  backLabel?: string;
  categories: TwoPaneCategory[];
  children: React.ReactNode;
  embedded?: boolean;
  footer?: React.ReactNode;
  onBack?: () => void;
  onCategoryChange: (categoryId: string) => void;
  onClose: () => void;
  onSearchChange: (query: string) => void;
  searchPlaceholder?: string;
  searchQuery: string;
  subtitle: string;
  title: string;
  wide?: boolean;
}) {
  const className = [
    "modal-card",
    "two-pane-selection-modal",
    embedded ? "embedded" : "",
    wide ? "wide" : "",
  ].filter(Boolean).join(" ");
  const content = (
    <div className={className} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : true} aria-labelledby="two-pane-selection-title">
      <div className="two-pane-selection-header">
        <div className="modal-header compact">
          <div>
            <h2 id="two-pane-selection-title">{title}</h2>
            <p>{subtitle}</p>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label={`Close ${title}`}>
            <X size={16} />
          </button>
        </div>
        <label className="two-pane-search">
          <Search size={16} />
          <input
            autoFocus
            placeholder={searchPlaceholder}
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
          />
        </label>
      </div>

      <div className="two-pane-selection-body">
        <nav className="two-pane-category-list" aria-label={`${title} categories`}>
          {categories.map((category) => {
            const Icon = category.icon;
            return (
              <button
                className={category.id === activeCategoryId ? "two-pane-category active" : "two-pane-category"}
                disabled={category.disabled}
                key={category.id}
                onClick={() => onCategoryChange(category.id)}
                type="button"
              >
                {Icon ? <Icon size={16} /> : null}
                <span>{category.label}</span>
                <Badge tone={category.count ? "blue" : "gray"}>{category.count}</Badge>
              </button>
            );
          })}
        </nav>
        <section className="two-pane-selection-content">{children}</section>
      </div>
      {footer || onBack ? (
        <div className="two-pane-selection-footer">
          {onBack ? (
            <button className="secondary-button two-pane-editor-back" onClick={onBack} type="button">
              <ArrowLeft size={15} /> {backLabel}
            </button>
          ) : null}
          {footer ? <div className="two-pane-selection-footer-actions">{footer}</div> : null}
        </div>
      ) : null}
    </div>
  );
  if (embedded) return content;
  return (
    <div className="modal-backdrop" role="presentation">
      {content}
    </div>
  );
}
