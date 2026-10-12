/**
 * Views feature (agent-built pages) — public surface used by the app shell.
 *
 * CONTRACT (owned by the views workstream; the shell only imports these):
 *   <ViewsNav collapsed onNavigate />  — sidebar section: pinned views + "New view"
 *   <ViewsPage route={route} />        — the whole `/views`, `/views/new` and `/views/:id` area
 */
export { ViewsNav } from './ViewsNav'
export { ViewsPage } from './ViewsPage'
