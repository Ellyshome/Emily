---
name: editable-web-slideshow
description: Transform static HTML slides into editable presentations—inline text editing, drag-to-reposition elements, localStorage persistence, and FOUC prevention. Use when the user asks to make a web-based PPT/slideshow editable, add drag-to-move for slide elements, save edits locally, or fix content flash on load. Do not use for native PowerPoint files or Markdown-based slides.
---

# Editable Web Slideshow

Turn a static HTML slideshow into an interactive, user-editable presentation. The skill covers inline text editing, element drag-to-move, localStorage persistence, cross-resolution stability, and preventing flash-of-unstyled-content.

## Core Design Principles

1. **Edit mode toggle, not permanent contenteditable** — Never add `contenteditable` to every DOM element. Use a global edit mode that toggles editability on demand.
2. **Separate concerns** — Text editing and element positioning are two distinct sub-modes. Don't mix them; let the user switch.
3. **Persistence without a server** — Use `localStorage` for saving edits. Split text content and layout positions into separate keys.
4. **Transform for movement** — Use `transform: translate()` for drag offsets, never `position: relative` + `left/top`. Transform does not disturb document flow or positioning contexts of child elements.
5. **Stable IDs, not selector-based indexing** — Identify elements by DOM path (slide index + sibling tag indices), not by class name + array index. Selector-based IDs break when the selector list changes.

## Step-by-Step Implementation

### Phase 1 — Text Edit Mode

**UI entry point:** Add an "Edit" button in the header toolbar + a keyboard shortcut (E key).

**Implementation:**
- Maintain an `editMode` boolean flag.
- When enabled, add `body.edit-mode` class and show an edit-mode indicator bar below the header.
- Batch-apply `contenteditable="true"` and `spellcheck="false"` to text-bearing elements (`h1-h6, p, span, li, div.font-*, div.text-*`, etc.) inside `.slide` containers.
- Skip buttons, links, inputs, icons (`<i>`).
- Listen for `input` events with debounced save (~500ms) + immediate save on `blur`.

**Saving text edits:**
- Save the full `innerHTML` of each `.slide` (by index) to `localStorage` under a versioned key like `webppt_edit_content_v1`.
- **Strip `contenteditable` and `spellcheck` attributes before saving** — clone the slide, remove those attributes, then serialize. Otherwise the loaded content stays editable even outside edit mode.

**Loading text edits:**
- On page init, read from localStorage and replace each slide's `innerHTML` before the user sees it.
- Use a FOUC-prevention mechanism (see Phase 4).

### Phase 2 — Drag-to-Move Mode

**Sub-mode toggle:** Inside edit mode, add a segmented control for "Edit Text" vs "Move Position".

**How to determine what's draggable:**
- Do NOT pre-scan with a fixed selector list. Selector lists are fragile and always miss nested elements.
- Instead, use **event delegation** on each `.slide` container: listen for `mousedown`, then walk up from `e.target` to find all meaningful ancestors.
- A "meaningful draggable element" meets these criteria:
  - Not a button, link, input, textarea, icon (`<i>`), svg, script, style
  - Has `getBoundingClientRect().width >= 30` and `height >= 16`
  - Has text content (trimmed length > 0) or contains an image/svg
- Collect the chain of all such ancestors from target up to (but not including) the `.slide`. Innermost first.

**Selection rules:**
- Default drag = outermost element in the chain (largest container)
- **Alt + drag** = innermost element in the chain (most specific)
- **Tab** = cycle inward (child → grandchild)
- **Shift + Tab** = cycle outward (parent → grandparent)
- Highlight the selected element with a solid outline + ID badge.

**Dragging mechanics:**
- Read current transform via `getComputedStyle(el).transform` + `new DOMMatrix(transform)`.
- On `mousedown`, record start position and current translate values.
- On `mousemove`, apply `transform: translate(tx, ty)`.
- Use `document`-level `mousemove`/`mouseup` listeners so dragging works even if the mouse leaves the element.

**Never force `position: relative`** on draggable elements. Transform works with any positioning and does not change the containing block for absolutely positioned children. Forcing `position: relative` breaks any child element that uses `position: absolute` relative to a different ancestor.

### Phase 3 — Persistence & Cross-Resolution Stability

**Stable element identification:**
- Generate a DOM-path ID: `s{slideIdx}_{tag0}{idx0}__{tag1}{idx1}__...`
  - `slideIdx` = index of the `.slide` among all slides
  - Each path segment = tag name (lowercase) + index among same-tag siblings
- Example: `s1_div0__div2__p0` = slide 1 → first div → third div → first p
- Provide two utility functions:
  - `getDragPathId(el)` — compute and cache on `el.dataset.dragId`
  - `findElementByDragId(id)` — walk the DOM tree to find the element

**Saving positions:**
- Store offset as **percentage of slide container dimensions**, not pixels.
- `offsetXPct = tx / slideRect.width * 100`
- `offsetYPct = ty / slideRect.height * 100`
- Save to localStorage under a versioned key like `webppt_drag_layout_v2`.
- Bump the version number whenever the ID scheme or storage format changes.

**Restoring positions:**
- Iterate all saved drag IDs, call `findElementByDragId`, and apply `transform: translate(tx, ty)`.
- On window resize, recalculate pixel offsets from percentages and reapply.
- Restore positions both in drag mode AND on initial page load (so layout changes persist even outside edit mode).

### Phase 4 — FOUC Prevention (No Flash of Original Content)

**Problem:** If you restore edits after `DOMContentLoaded`, users see the original text flash before the edits appear.

**Solution:**
1. Add CSS: `body.slides-loading .slide-container { opacity: 0; }` and `body.slides-ready .slide-container { opacity: 1; transition: opacity 0.2s ease; }`
2. Start the `<body>` with class `slides-loading`.
3. In an inline script (or at the end of body), synchronously:
   - Restore text edits from localStorage
   - Use `requestAnimationFrame` to restore layout positions (after layout is calculated)
   - Then swap `slides-loading` → `slides-ready`
4. Edit mode preference restores **after** the reveal, so it doesn't block the first paint.

### Phase 5 — Clean Exit & Printing

- On exiting edit mode, remove all `contenteditable` attributes, `draggable-item` classes, and event listeners.
- In `@media print`, hide the edit bar, edit button, drag outlines, and drag hint overlays.
- Provide two reset actions:
  - **Reset layout** — clears only position data (keeps text edits)
  - **Reset all** — clears both text and layout (confirm dialog required)

## Common Pitfalls & Fixes

| Pitfall | Cause | Fix |
|---------|-------|-----|
| Nested elements can't be selected for dragging | Pre-scanning with selectors misses nested items; event bubbling always grabs the outer container | Use event delegation + ancestor-chain walking; Alt-drag for innermost |
| Child elements jump position when entering drag mode | Forcing `position: relative` changes the containing block for `absolute` children | Use `transform` only; never modify `position` |
| Saved positions don't restore correctly on different screen sizes | Saving pixel offsets directly | Save as percentage of slide container dimensions |
| Original text flashes before edits appear | Restoring edits asynchronously (DOMContentLoaded + setTimeout) | Start with `opacity: 0`, restore synchronously, then fade in |
| Old saved positions break after code changes | Storage format or ID scheme changed without version bump | Use versioned localStorage keys; bump version on format changes |
| Edit tool appears in print/PDF export | Edit UI isn't hidden in print media query | Add `#edit-mode-bar, #btn-edit` to `@media print { display: none !important; }` |
| Drag ID mismatch between save and restore | Different algorithms for generating IDs on save vs restore | Use a single pair of functions: `getDragPathId` / `findElementByDragId` |

## File Structure Reference

When applying this to an HTML slideshow:

```
<script>
  // State
  const EDIT_STORAGE_KEY = 'webppt_edit_content_v1'
  const DRAG_STORAGE_KEY = 'webppt_drag_layout_v2'
  let editMode = false
  let dragSubMode = 'text' // 'text' | 'drag'
  let selectedDragEl = null
  let currentDragging = null

  // Phase 1: Text edit
  function setEditMode(enabled) { ... }
  function getEditableElements() { ... }
  function saveEdits() { ... }
  function loadEdits() { ... }

  // Phase 2: Drag
  function enableDragMode() { ... }
  function disableDragMode() { ... }
  function isDraggableElement(el) { ... }
  function getDraggableChain(target) { ... }
  function getDragPathId(el) { ... }
  function findElementByDragId(id) { ... }
  function onSlideMouseDown(e) { ... }
  function startDrag(el, e) { ... }
  function onDragMove(e) { ... }
  function onDragEnd(e) { ... }
  function onDragKeyDown(e) { ... }

  // Phase 3: Persistence
  function saveDragPosition(el) { ... }
  function restoreDragPositions() { ... }
  function applySavedLayout() { ... }

  // Phase 4: Init (no FOUC)
  (function init() {
    loadEdits()
    requestAnimationFrame(() => {
      applySavedLayout()
      document.body.classList.remove('slides-loading')
      document.body.classList.add('slides-ready')
    })
  })()
</script>
```
