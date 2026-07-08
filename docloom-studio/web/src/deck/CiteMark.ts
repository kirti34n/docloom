import { Mark, mergeAttributes } from '@tiptap/core'

/** A citation mark carrying a source id. Rendered as a superscript chip; the
 *  number/known-state is stamped by the editor from the sources list. */
export const CiteMark = Mark.create({
  name: 'cite',
  inclusive: false,
  keepOnSplit: true,

  addAttributes() {
    return {
      sourceId: {
        default: null,
        parseHTML: (el) => (el as HTMLElement).getAttribute('data-cite'),
        renderHTML: (attrs) => (attrs.sourceId ? { 'data-cite': attrs.sourceId } : {}),
      },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-cite]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return ['span', mergeAttributes(HTMLAttributes, { class: 'rt-cite-mark' }), 0]
  },
})
