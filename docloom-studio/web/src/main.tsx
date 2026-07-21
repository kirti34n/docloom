import { lazy, StrictMode, Suspense, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider, useParams } from 'react-router'
import { Loader2 } from 'lucide-react'
import { api } from './api/client'
// Self-hosted fonts (bundled by Vite; no CDN, works offline). Fraunces is the
// display face, IBM Plex Sans/Mono are body/UI/utility. The rest are the deck
// theme faces (aurora/slate lean on Sora+Inter, paper on Inter, editorial on
// Fraunces+Source Serif 4, pulse on Space Grotesk, terra on Bricolage
// Grotesque+Nunito Sans) so every theme renders in its real typeface.
import '@fontsource/fraunces/400.css'
import '@fontsource/fraunces/500.css'
import '@fontsource/fraunces/600.css'
import '@fontsource/ibm-plex-sans/400.css'
import '@fontsource/ibm-plex-sans/500.css'
import '@fontsource/ibm-plex-sans/600.css'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/sora/500.css'
import '@fontsource/sora/600.css'
import '@fontsource/sora/700.css'
import '@fontsource/source-serif-4/400.css'
import '@fontsource/source-serif-4/600.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/600.css'
import '@fontsource/space-grotesk/700.css'
import '@fontsource/bricolage-grotesque/400.css'
import '@fontsource/bricolage-grotesque/600.css'
import '@fontsource/bricolage-grotesque/700.css'
import '@fontsource/nunito-sans/400.css'
import '@fontsource/nunito-sans/600.css'
import '@fontsource/nunito-sans/700.css'
import './index.css'
import { Shell } from './App'
import { AuthProvider } from './auth/AuthContext'
import { AuthGate } from './auth/AuthGate'
import { NotebooksList } from './screens/NotebooksList'
import { Settings } from './screens/Settings'
import { AssetLibrary } from './screens/AssetLibrary'
import { NotebookWorkspace } from './screens/NotebookWorkspace'
import { DeckEditor } from './screens/DeckEditor'
import { DocEditor } from './screens/DocEditor'
import { SheetEditor } from './screens/SheetEditor'
import { DiagramEditor } from './screens/DiagramEditor'
import { InfographicEditor } from './screens/InfographicEditor'
import { PodcastEditor } from './screens/PodcastEditor'
import { PresentMode } from './deck/PresentMode'

// The IR canvas (Excalidraw) is a large chunk; lazy-load it so it only ships
// to the browser for diagram artifacts that actually use it (below).
const DiagramIRCanvas = lazy(() =>
  import('./screens/DiagramIRCanvas').then((m) => ({ default: m.DiagramIRCanvas })))

const routeLoader = (
  <div className="flex h-full items-center justify-center bg-stage-bg text-stage-muted">
    <Loader2 className="animate-spin" />
  </div>
)

/** A diagram artifact's payload shape decides which editor mounts: the new
 *  coordinate-free IR canvas (payload.type === 'diagram_ir', or a bare
 *  `diagram_ir` key), or the legacy hand-written D2 editor for every diagram
 *  authored before it (`source`/`mermaid_src`). This is a *routing* decision
 *  only -- each editor still fetches the artifact itself for its own state
 *  (docs/editor-design.md section 4a). */
function DiagramRoute() {
  const { artifactId } = useParams()
  const [kind, setKind] = useState<'loading' | 'ir' | 'legacy'>('loading')

  useEffect(() => {
    if (!artifactId) return
    let cancelled = false
    setKind('loading')
    api.get<{ payload?: { type?: string; diagram_ir?: unknown } }>(`/api/artifacts/${artifactId}`)
      .then((a) => {
        if (cancelled) return
        const p = a.payload ?? {}
        setKind(p.type === 'diagram_ir' || p.diagram_ir ? 'ir' : 'legacy')
      })
      // let the legacy editor's own load path surface the real error
      .catch(() => { if (!cancelled) setKind('legacy') })
    return () => { cancelled = true }
  }, [artifactId])

  if (kind === 'loading') return routeLoader
  if (kind === 'ir') {
    return (
      <Suspense fallback={routeLoader}>
        <DiagramIRCanvas />
      </Suspense>
    )
  }
  return <DiagramEditor />
}

const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <NotebooksList /> },
      { path: 'n/:notebookId', element: <NotebookWorkspace /> },
      { path: 'n/:notebookId/deck/:artifactId', element: <DeckEditor /> },
      { path: 'n/:notebookId/doc/:artifactId', element: <DocEditor /> },
      { path: 'n/:notebookId/sheet/:artifactId', element: <SheetEditor /> },
      { path: 'n/:notebookId/diagram/:artifactId', element: <DiagramRoute /> },
      { path: 'n/:notebookId/infographic/:artifactId', element: <InfographicEditor /> },
      { path: 'n/:notebookId/podcast/:artifactId', element: <PodcastEditor /> },
      { path: 'assets', element: <AssetLibrary /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
  // Present mode renders full-viewport with no app chrome, so it lives outside
  // the Shell layout rather than inside its <main> outlet (a fixed overlay
  // nested in the shell is trapped by the shell's own layout containing block).
  { path: '/n/:notebookId/deck/:artifactId/present', element: <PresentMode /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AuthProvider>
      <AuthGate>
        <RouterProvider router={router} />
      </AuthGate>
    </AuthProvider>
  </StrictMode>,
)
