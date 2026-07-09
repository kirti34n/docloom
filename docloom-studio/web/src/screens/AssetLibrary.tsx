import { useEffect, useRef, useState } from 'react'
import { Trash2, Upload } from 'lucide-react'
import { api } from '../api/client'

interface Asset {
  id: string
  type: string
  filename: string
  tags: string
}
interface Brand {
  accent?: string | null
  logo_asset_id?: string | null
  heading_family?: string | null
  heading_asset_id?: string | null
  body_family?: string | null
  body_asset_id?: string | null
}

export function AssetLibrary() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [brand, setBrand] = useState<Brand>({})
  const [note, setNote] = useState<string | null>(null)
  const [uploadType, setUploadType] = useState('image')
  const fileInput = useRef<HTMLInputElement>(null)

  const load = () => api.get<Asset[]>('/api/assets').then(setAssets)
  useEffect(() => {
    load()
    api.get<Brand>('/api/brand-kit').then(setBrand)
  }, [])

  const upload = async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('type', uploadType)
    const res = await fetch('/api/assets', { method: 'POST', body: fd })
    const data = await res.json()
    if (data.font_note) setNote(data.font_note)
    load()
  }

  const setTags = async (id: string, tags: string) => {
    setAssets((a) => a.map((x) => (x.id === id ? { ...x, tags } : x)))
    await api.patch(`/api/assets/${id}`, { tags })
  }
  const remove = async (id: string) => {
    await api.del(`/api/assets/${id}`)
    load()
  }
  const saveBrand = async (next: Brand) => {
    setBrand(next)
    await api.put('/api/brand-kit', next)
  }

  const images = assets.filter((a) => a.type === 'image' || a.type === 'logo')
  const fonts = assets.filter((a) => a.type === 'font')

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-xl font-semibold">Assets</h1>
          <p className="mt-1 text-[13px] text-ws-muted">
            Logos, images, and fonts. Generation pulls from these — tag images so
            the right one lands in each slide.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select value={uploadType} onChange={(e) => setUploadType(e.target.value)}
            className="rounded-lg border border-ws-line bg-ws-panel px-2.5 py-2 text-[13px]">
            <option value="image">Image</option>
            <option value="logo">Logo</option>
            <option value="font">Font</option>
          </select>
          <button onClick={() => fileInput.current?.click()}
            className="flex items-center gap-1.5 rounded-lg bg-ws-ink px-3 py-2 text-[13px] font-medium text-white">
            <Upload size={14} /> Upload
          </button>
          <input ref={fileInput} type="file" hidden
            accept={uploadType === 'font' ? '.ttf,.otf,.woff,.woff2' : 'image/*'}
            onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        </div>
      </div>

      {note && (
        <div className="mt-4 rounded-lg border border-ws-line bg-ws-panel px-3 py-2 text-[12px] text-ws-muted">
          {note} <button onClick={() => setNote(null)} className="ml-2 underline">dismiss</button>
        </div>
      )}

      {/* brand kit */}
      <div className="mt-6 rounded-xl border border-ws-line bg-ws-panel p-4">
        <h2 className="text-[13px] font-semibold">Brand kit</h2>
        <p className="mt-0.5 text-[12px] text-ws-muted">Applied to every generation and export.</p>
        <div className="mt-3 flex flex-wrap items-center gap-6">
          <label className="flex items-center gap-2 text-[13px]">
            Accent
            <input type="color" value={brand.accent ?? '#3b5bdb'}
              onChange={(e) => saveBrand({ ...brand, accent: e.target.value })}
              className="h-8 w-12 rounded border border-ws-line bg-transparent" />
            {brand.accent && (
              <button onClick={() => saveBrand({ ...brand, accent: null })}
                className="text-[12px] text-ws-muted underline">clear</button>
            )}
          </label>
          <label className="flex items-center gap-2 text-[13px]">
            Logo
            <select value={brand.logo_asset_id ?? ''}
              onChange={(e) => saveBrand({ ...brand, logo_asset_id: e.target.value || null })}
              className="rounded-lg border border-ws-line bg-ws-bg px-2 py-1.5 text-[13px]">
              <option value="">None</option>
              {images.map((a) => <option key={a.id} value={a.id}>{a.filename}</option>)}
            </select>
          </label>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <FontRow label="Heading font" family={brand.heading_family}
            assetId={brand.heading_asset_id} fonts={fonts}
            onFamily={(v) => saveBrand({ ...brand, heading_family: v })}
            onAsset={(v) => saveBrand({ ...brand, heading_asset_id: v })} />
          <FontRow label="Body font" family={brand.body_family}
            assetId={brand.body_asset_id} fonts={fonts}
            onFamily={(v) => saveBrand({ ...brand, body_family: v })}
            onAsset={(v) => saveBrand({ ...brand, body_asset_id: v })} />
        </div>
        <p className="mt-2 text-[11px] text-ws-muted">
          Fonts embed in PDF & HTML exports. PowerPoint stores the font name only —
          install the font locally to see it in PPTX.
        </p>
      </div>

      {/* asset grid */}
      {assets.length === 0 ? (
        <div className="mt-16 rounded-xl border border-dashed border-ws-line p-12 text-center text-[13px] text-ws-muted">
          No assets yet. Upload logos and images, then tag them — a deck about
          “remote teams” will pull an image tagged “team”.
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {assets.map((a) => (
            <div key={a.id} className="group overflow-hidden rounded-xl border border-ws-line bg-ws-panel">
              <div className="flex aspect-video items-center justify-center overflow-hidden bg-ws-bg">
                {a.type === 'font' ? (
                  <span className="px-2 text-center text-[12px] text-ws-muted">{a.filename}</span>
                ) : (
                  <img src={`/api/assets/${a.id}/file`} alt={a.filename}
                    className="h-full w-full object-contain" />
                )}
              </div>
              <div className="p-2.5">
                <div className="flex items-center justify-between gap-1">
                  <span className="truncate text-[12px] font-medium" title={a.filename}>{a.filename}</span>
                  <button onClick={() => remove(a.id)} className="shrink-0 text-ws-muted hover:text-ws-danger">
                    <Trash2 size={13} />
                  </button>
                </div>
                <input value={a.tags} onChange={(e) => setTags(a.id, e.target.value)}
                  placeholder="tags, comma separated"
                  className="mt-1.5 w-full rounded border border-ws-line bg-ws-bg px-2 py-1 text-[11px] outline-none" />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function FontRow({
  label, family, assetId, fonts, onFamily, onAsset,
}: {
  label: string
  family?: string | null
  assetId?: string | null
  fonts: Asset[]
  onFamily: (v: string | null) => void
  onAsset: (v: string | null) => void
}) {
  return (
    <div className="rounded-lg border border-ws-line bg-ws-bg p-3">
      <div className="text-[12px] font-medium">{label}</div>
      <div className="mt-2 flex flex-col gap-2">
        <input
          value={family ?? ''}
          onChange={(e) => onFamily(e.target.value || null)}
          placeholder="Font family name (e.g. Inter)"
          className="w-full rounded border border-ws-line bg-ws-panel px-2 py-1.5 text-[12px] outline-none focus:border-ws-accent"
        />
        <select value={assetId ?? ''}
          onChange={(e) => onAsset(e.target.value || null)}
          className="w-full rounded border border-ws-line bg-ws-panel px-2 py-1.5 text-[12px]">
          <option value="">Embed file: none (name only)</option>
          {fonts.map((a) => <option key={a.id} value={a.id}>{a.filename}</option>)}
        </select>
      </div>
    </div>
  )
}
