import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
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
      { path: 'n/:notebookId/diagram/:artifactId', element: <DiagramEditor /> },
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
