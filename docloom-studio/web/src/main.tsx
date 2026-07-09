import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
// Self-hosted editorial-studio fonts (bundled by Vite; no CDN, works offline).
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/sora/500.css'
import '@fontsource/sora/600.css'
import '@fontsource/sora/700.css'
import '@fontsource/jetbrains-mono/400.css'
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
