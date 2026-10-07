type Pagination = { total: number; loading: boolean; next_cursor: string | null; canPrevious: boolean; next: () => void; previous: () => void; error: string };
export function DirectoryPagination({ page }: { page: Pagination }) {
  return <div className="directory-pagination" aria-label="Directory pages">
    <span aria-live="polite">{page.loading ? "Loading directory…" : `${page.total} results`}</span>
    <button className="secondary-button" type="button" aria-label="Previous directory page" disabled={!page.canPrevious || page.loading} onClick={page.previous}>Previous</button>
    <button className="secondary-button" type="button" aria-label="Next directory page" disabled={!page.next_cursor || page.loading} onClick={page.next}>Next</button>
    {page.error ? <span className="auth-error" role="alert">{page.error}</span> : null}
  </div>;
}
