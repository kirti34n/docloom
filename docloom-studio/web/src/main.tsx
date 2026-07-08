import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import './index.css'
import { Shell } from './App'
import { NotebooksList } from './screens/NotebooksList'
import { Settings } from './screens/Settings'
import { AssetLibrary } from './screens/AssetLibrary'
import { NotebookWorkspace } from './screens/NotebookWorkspace'
import { DeckEditor } from './screens/DeckEditor'
import { DocEditor } from './screens/DocEditor'
import { SheetEditor } from './screens/SheetEditor'
import { DiagramEditor } from './screens/DiagramEditor'
import { InfographicEditor } from './screens/InfographicEditor'

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
      { path: 'assets', element: <AssetLibrary /> },
      { path: 'settings', element: <Settings /> },
    ],
  },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
