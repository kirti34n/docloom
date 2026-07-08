import { useEditor, EditorContent } from '@tiptap/react'
import type { Editor } from '@tiptap/core'
import Document from '@tiptap/extension-document'
import StarterKit from '@tiptap/starter-kit'
import { useEffect, useRef } from 'react'
import { CiteMark } from './CiteMark'
import {
  docToListItems,
  docToRichText,
  listItemsToDoc,
  richTextToDoc,
} from './serialize'
import type { ListItem, RichText } from './types'

const SingleParagraph = Document.extend({ content: 'paragraph' })

const INLINE_KIT = StarterKit.configure({
  document: false,
  heading: false,
  bulletList: false,
  orderedList: false,
  listItem: false,
  blockquote: false,
  codeBlock: false,
  horizontalRule: false,
  strike: false,
  underline: false,
  link: { openOnClick: false, autolink: false },
})

const LIST_KIT = StarterKit.configure({
  heading: false,
  blockquote: false,
  codeBlock: false,
  horizontalRule: false,
  strike: false,
  underline: false,
  link: { openOnClick: false, autolink: false },
})

/** Shared "uncontrolled while focused; external writes bump extRev" wiring.
 *  onCommit fires debounced during editing and on blur. */
function useCommit(
  editor: Editor | null,
  extRev: number,
  toExternal: () => void,
  onCommit: () => void,
) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const focused = useRef(false)

  useEffect(() => {
    if (!editor) return
    const onUpdate = () => {
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(onCommit, 350)
    }
    const onFocus = () => (focused.current = true)
    const onBlur = () => {
      focused.current = false
      if (timer.current) clearTimeout(timer.current)
      onCommit()
    }
    editor.on('update', onUpdate)
    editor.on('focus', onFocus)
    editor.on('blur', onBlur)
    return () => {
      editor.off('update', onUpdate)
      editor.off('focus', onFocus)
      editor.off('blur', onBlur)
    }
  }, [editor, onCommit])

  // external mutation → reset content, unless the user is mid-edit
  useEffect(() => {
    if (editor && !focused.current) toExternal()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extRev])
}

export function EditableText({
  value,
  extRev = 0,
  onChange,
  className,
  placeholder,
}: {
  value: RichText
  extRev?: number
  onChange: (rt: RichText) => void
  className?: string
  placeholder?: string
}) {
  const editor = useEditor({
    extensions: [SingleParagraph, INLINE_KIT, CiteMark],
    content: richTextToDoc(value),
    editorProps: { attributes: { class: `editable ${className ?? ''}` } },
  })

  useCommit(
    editor,
    extRev,
    () => editor?.commands.setContent(richTextToDoc(value)),
    () => {
      if (editor) onChange(docToRichText(editor.getJSON() as never))
    },
  )

  return <EditorContent editor={editor} data-placeholder={placeholder} />
}

export function EditableList({
  items,
  ordered,
  extRev = 0,
  onChange,
}: {
  items: ListItem[]
  ordered: boolean
  extRev?: number
  onChange: (items: ListItem[]) => void
}) {
  const editor = useEditor({
    extensions: [LIST_KIT, CiteMark],
    content: listItemsToDoc(items, ordered),
    editorProps: { attributes: { class: 'editable editable-list' } },
  })

  useCommit(
    editor,
    extRev,
    () => editor?.commands.setContent(listItemsToDoc(items, ordered)),
    () => {
      if (editor) onChange(docToListItems(editor.getJSON() as never))
    },
  )

  return <EditorContent editor={editor} />
}
