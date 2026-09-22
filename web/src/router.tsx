import { createRootRoute, createRoute, createRouter, redirect } from '@tanstack/react-router'
import { Shell } from './app/Shell'
import { Home } from './features/work/Home'
import { WorkspacePage } from './features/pipeline/Workspace'
import { PhasePage } from './features/pipeline/Phase'
import { ReviewPage } from './features/review/Review'
import { ReaderPage } from './features/reader/Reader'
const search = (raw: Record<string,unknown>): Partial<Record<'filter'|'q'|'category'|'status'|'term'|'section'|'chapter',string>> => ({
  filter: typeof raw.filter === 'string' ? raw.filter : undefined,
  q: typeof raw.q === 'string' ? raw.q : undefined,
  category: typeof raw.category === 'string' ? raw.category : undefined,
  status: typeof raw.status === 'string' ? raw.status : undefined,
  term: typeof raw.term === 'string' ? raw.term : undefined,
  section: typeof raw.section === 'string' ? raw.section : undefined,
  chapter: typeof raw.chapter === 'string' ? raw.chapter : undefined,
})
const rootRoute = createRootRoute({ component: Shell, validateSearch: search,
  notFoundComponent: () => <main className="main"><h1>Page not found</h1><a href="/work">Return to Work</a></main>,
  errorComponent: ({ reset }) => <main className="main"><h1>This view could not load</h1><button onClick={reset}>Try again</button></main> })
const index = createRoute({ getParentRoute: () => rootRoute, path: '/', beforeLoad: () => { throw redirect({ to: '/work' }) } })
const work = createRoute({ getParentRoute: () => rootRoute, path: '/work', component: Home })
const workspace = createRoute({ getParentRoute: () => rootRoute, path: '/work/workspaces/$workspaceId', component: WorkspacePage, remountDeps: ({params}) => params.workspaceId })
const phase = createRoute({ getParentRoute: () => rootRoute, path: '/work/workspaces/$workspaceId/$phase', component: PhasePage,
  beforeLoad: ({ params }) => { if (params.phase === 'review') throw redirect({ to: '/work/workspaces/$workspaceId/review', params: { workspaceId: params.workspaceId } }); if (!['prepare','analyse','translate','publish'].includes(params.phase)) throw redirect({ to: '/work/workspaces/$workspaceId', params: { workspaceId: params.workspaceId } }) } })
const review = createRoute({ getParentRoute: () => rootRoute, path: '/work/workspaces/$workspaceId/review', component: ReviewPage, remountDeps: ({params}) => params.workspaceId })
const reader = createRoute({ getParentRoute: () => rootRoute, path: '/reader', component: ReaderPage })
const book = createRoute({ getParentRoute: () => rootRoute, path: '/reader/$workspaceId', component: ReaderPage })
export const router = createRouter({ routeTree: rootRoute.addChildren([index,work,workspace,phase,review,reader,book]), scrollRestoration: true })
declare module '@tanstack/react-router' { interface Register { router: typeof router } }
