import { useModalFocus } from "../ui/useModalFocus";
import { useModalClose } from "../ui/useModalClose";
import { ArrowRight, Bell, CalendarDays, Car, ClipboardPaste, Clock3, Command, GitBranch, Loader2, Search, UserRound, Users, X } from "lucide-react";
import React from "react";
import { createPortal } from "react-dom";
import { searchApi, type GlobalSearchResult, type SearchPaletteItem } from "../api/search";
import type { UserAccount, ViewKey } from "../api/types";
import { Badge } from "../ui/primitives";
import { navigationItems } from "./navigation";

type SearchMeta = { icon: React.ElementType; label: string };
const searchMeta: Record<SearchPaletteItem["type"], SearchMeta> = {
  access_event: { icon: CalendarDays, label: "Event" }, alert: { icon: Bell, label: "Alert" }, automation_rule: { icon: GitBranch, label: "Automation" }, group: { icon: Users, label: "Group" }, notification_rule: { icon: Bell, label: "Notification" }, person: { icon: UserRound, label: "Person" }, schedule: { icon: Clock3, label: "Schedule" }, shortcut: { icon: Command, label: "Shortcut" }, user: { icon: Users, label: "User" }, vehicle: { icon: Car, label: "Vehicle" }, visitor_pass: { icon: ClipboardPaste, label: "Pass" }
};

export function SearchPalette({ currentUser, initialQuery, open, onClose: finishClose, onOpenResult: finishOpenResult }: { currentUser: UserAccount; initialQuery: string; open: boolean; onClose: () => void; onOpenResult: (result: SearchPaletteItem) => void }) {
  const modalRef = React.useRef<HTMLElement>(null);
  const onClose = useModalClose(modalRef, finishClose);
  const onOpenResult = useModalClose(modalRef, finishOpenResult);
  useModalFocus(modalRef, open, onClose);
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = React.useState(initialQuery);
  const [results, setResults] = React.useState<GlobalSearchResult[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [previewItem, setPreviewItem] = React.useState<SearchPaletteItem | null>(null);
  const shortcuts = React.useMemo(() => searchShortcuts(currentUser), [currentUser]);
  const trimmedQuery = query.trim();
  const items: SearchPaletteItem[] = trimmedQuery ? results : shortcuts;
  const activeItem = items[Math.min(activeIndex, Math.max(0, items.length - 1))] ?? null;
  const completion = searchCompletion(query, results);
  React.useLayoutEffect(() => {
    if (!open) return;
    setQuery(initialQuery); setResults([]); setError(""); setLoading(false); setActiveIndex(0); setPreviewItem(null);
    inputRef.current?.focus({ preventScroll: true });
    inputRef.current?.select();
  }, [initialQuery, open]);

  React.useEffect(() => {
    if (!open) return undefined;
    if (!trimmedQuery) { setResults([]); setLoading(false); setError(""); return undefined; }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true); setError("");
      try { setResults(await searchApi.search(trimmedQuery, { signal: controller.signal })); }
      catch (searchError) { if (!controller.signal.aborted) setError(searchError instanceof Error ? searchError.message : "Search failed"); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }, 180);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [open, trimmedQuery]);
  React.useEffect(() => { setActiveIndex(0); setPreviewItem(null); }, [trimmedQuery]);
  React.useEffect(() => { if (activeIndex >= items.length) setActiveIndex(Math.max(0, items.length - 1)); }, [activeIndex, items.length]);
  if (!open) return null;
  const moveActive = (delta: number) => { if (!items.length) return; setActiveIndex((current) => (current + delta + items.length) % items.length); setPreviewItem(null); };
  const selectItem = (item: SearchPaletteItem) => {
    if (window.matchMedia("(max-width: 720px)").matches) onOpenResult(item);
    else setPreviewItem(item);
  };
  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); moveActive(event.key === "ArrowDown" ? 1 : -1); return; }
    if (event.key === "Tab" && completion) { event.preventDefault(); setQuery(completion); return; }
    if (event.key === "Enter") { event.preventDefault(); if (!activeItem) return; event.metaKey || event.ctrlKey ? onOpenResult(activeItem) : selectItem(activeItem); }
  };
  return createPortal(
    <div className="search-palette-backdrop" onMouseDown={onClose} role="presentation">
      <section ref={modalRef} aria-label="Global search" aria-modal="true" className="search-palette" onMouseDown={(event) => event.stopPropagation()} role="dialog">
        <div className="search-palette-input-shell">
          <Search size={20} />
          <div className="search-palette-input-stack"><input aria-label="Search anything" aria-expanded="true" aria-activedescendant={activeItem ? `global-search-result-${activeItem.type}-${activeItem.id}` : undefined} aria-autocomplete="list" aria-controls="global-search-results" autoComplete="off" onChange={(event) => setQuery(event.target.value)} onKeyDown={handleInputKeyDown} placeholder="Search anything…" ref={inputRef} role="combobox" spellCheck={false} value={query} />{completion ? <span className="search-palette-completion">{completion}</span> : null}</div>
          {loading ? <Loader2 className="spin" size={18} /> : null}
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close search"><X size={18} /></button>
        </div>
        <div className="search-palette-body">
          <div className="search-palette-results" id="global-search-results" aria-label="Search results" role="listbox">
            {error ? <div className="search-palette-state">{error}</div> : null}
            {!error && items.length ? items.map((item, index) => <SearchPaletteRow active={index === activeIndex} item={item} key={`${item.type}-${item.id}`} onClick={() => { setActiveIndex(index); selectItem(item); }} onMouseEnter={() => setActiveIndex(index)} />) : null}
            {!error && !items.length && !loading ? <div className="search-palette-state">No results</div> : null}
          </div>
          <SearchPalettePreview item={previewItem ?? activeItem} onOpen={onOpenResult} />
        </div>
      </section>
    </div>, document.body
  );
}

function SearchPaletteRow({ active, item, onClick, onMouseEnter }: { active: boolean; item: SearchPaletteItem; onClick: () => void; onMouseEnter: () => void }) {
  const Icon = searchMeta[item.type]?.icon ?? Search;
  return <button aria-selected={active} className={active ? "search-palette-row active" : "search-palette-row"} id={`global-search-result-${item.type}-${item.id}`} onClick={onClick} onMouseEnter={onMouseEnter} role="option" type="button"><span className={`search-palette-row-icon ${item.type}`}><Icon size={17} /></span><span className="search-palette-row-main"><strong>{item.label}</strong><small>{item.subtitle}</small></span><span className="search-palette-row-type">{searchMeta[item.type]?.label ?? "Search"}</span><ArrowRight aria-hidden="true" className="search-palette-row-arrow" size={16} /></button>;
}

function SearchPalettePreview({ item, onOpen }: { item: SearchPaletteItem | null; onOpen: (item: SearchPaletteItem) => void }) {
  if (!item) return <aside className="search-palette-preview empty"><Search size={22} /></aside>;
  const Icon = searchMeta[item.type]?.icon ?? Search;
  return <aside className="search-palette-preview"><div className="search-palette-preview-title"><span className={`search-palette-row-icon ${item.type}`}><Icon size={18} /></span><div><span>{searchMeta[item.type]?.label ?? "Search"}</span><h2>{item.preview.title}</h2></div></div>{item.preview.body ? <p>{item.preview.body}</p> : null}{item.preview.badges.length ? <div className="search-palette-badges">{item.preview.badges.map((badge) => <Badge key={badge} tone="gray">{badge}</Badge>)}</div> : null}{item.preview.facts.length ? <dl className="search-palette-facts">{item.preview.facts.map((fact) => <div key={`${fact.label}-${fact.value}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl> : null}<button className="primary-button search-palette-open" onClick={() => onOpen(item)} type="button">Open<ArrowRight size={16} /></button></aside>;
}

function searchShortcuts(currentUser: UserAccount): SearchPaletteItem[] {
  return navigationItems.filter((item) => !item.adminOnly || currentUser.role === "admin").map((item) => searchShortcut(item.key, item.label, item.group));
}
function searchShortcut(view: ViewKey, label: string, subtitle = "Open view"): SearchPaletteItem { return { id: view, type: "shortcut", label, subtitle, filter_value: "", target: { view }, preview: { title: label, body: null, badges: ["Shortcut"], facts: [] } }; }
function searchCompletion(query: string, results: GlobalSearchResult[]) {
  const trimmed = query.trim(); const first = results[0];
  if (!trimmed || !first) return "";
  const normalizedQuery = trimmed.toLowerCase();
  return [first.filter_value, first.label].filter(Boolean).find((candidate) => candidate.toLowerCase().startsWith(normalizedQuery) && candidate.length > trimmed.length) ?? "";
}
