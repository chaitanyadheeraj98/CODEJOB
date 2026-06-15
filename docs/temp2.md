## 1. High-level overview of the application

The Resume Database is already working functionally. The problem is only **UI design**: every resume card shows all stored skills, textarea, matching skills, toggle, save, and delete controls at once. That makes the Settings page very long and hard to navigate.

So this fix should be **frontend-only**. No backend/database change is needed.

---

## 2. Repository structure

Relevant area:

```txt id="r8tp1m"
dashboard/src/App.tsx
dashboard/src/App.css
```

The current `ResumeDatabaseSection` already receives:

```txt id="js9qx5"
activeResume
resumeAssets
resumeSkillEdits
uploadResume
saveResumeSkills
toggleResumeAsset
deleteResumeAsset
```

Then it loops through `resumeAssets` and renders every resume card fully expanded.

---

## 3. Main technologies used

This part is plain React + CSS:

```txt id="eeh1yc"
React state controls which resume card is expanded/collapsed.
CSS controls the compact card layout.
Backend APIs stay the same.
```

---

## 4. Core feature to improve

Current behavior:

```txt id="d1r0et"
Resume card always shows:
- filename
- version
- added date
- current/enabled status
- full stored skills textarea
- full matching skills paragraph
- enabled toggle
- save skills button
- delete button
```

Desired behavior:

```txt id="l0mbnm"
Resume card collapsed by default:
- show only filename/version/status
- show short skills preview
- show expand/collapse button

When expanded:
- show stored skills textarea
- full matching skills
- enable toggle
- save skills
- delete
```

---

## 5. Architecture and code flow

No backend logic changes.

Current flow remains:

```txt id="3jqh6o"
GET /settings/resumes
↓
resumeAssets state
↓
ResumeDatabaseSection
↓
resumeAssets.map(...)
↓
render each resume card
```

The only added frontend state should be something like:

```tsx id="8vb1kv"
const [expandedResumeIds, setExpandedResumeIds] = useState<Record<number, boolean>>({})
```

Then each resume card checks:

```tsx id="uz8w8h"
const isExpanded = !!expandedResumeIds[resume.id]
```

Collapsed card shows a compact summary. Expanded card shows the existing detailed UI.

---

## 6. Important files and folders

### `dashboard/src/App.tsx`

Plan:

1. Add `expandedResumeIds` state near other resume UI state.
2. Pass it into `ResumeDatabaseSection`.
3. Add a `toggleResumeExpanded(resumeId)` handler.
4. Inside `resumeAssets.map(...)`, render:

   * compact header always visible
   * detailed body only when expanded

Suggested component behavior:

```txt id="f6mdad"
Resume Card Header:
[filename] [v20] [Current] [Enabled] [Expand/Collapse]

Collapsed body:
Matching skills: Java, Spring Boot, Microservices, AWS... +12 more

Expanded body:
Stored Skills textarea
Full Matching Skills
Enable toggle
Save Skills
Delete
```

### `dashboard/src/App.css`

Add styles for:

```txt id="no9kwb"
.resumeDatabaseItemCollapsed
.resumeDatabaseCardHeader
.resumeDatabaseSummary
.resumeDatabaseBody
.resumeDatabaseExpandButton
.resumeDatabaseBadges
```

Existing styling already has resume database class names in `App.css`, so the new styles should extend the current design instead of replacing it.

---

## 7. How the application likely runs after the change

User flow after fix:

```txt id="rzhsz0"
Open Settings
↓
Resume Database shows compact resume cards
↓
User clicks a resume card / chevron
↓
That card expands
↓
User edits skills, saves, toggles enabled, or deletes
↓
Other cards remain collapsed
```

Upload flow should stay the same:

```txt id="s7ltrg"
Choose file
Enter skills
Upload Resume To Database
↓
loadResumes()
↓
new resume appears in the list
```

Optional nice behavior: after upload, auto-expand the newest uploaded resume so the user can immediately verify skills.

---

## 8. Key observations for the human

This is the safest fix because:

```txt id="wtbse0"
- No backend change needed
- No DB migration needed
- No API change needed
- Existing upload/save/toggle/delete logic remains untouched
- Only the card presentation changes
```

Best implementation plan:

```txt id="b1y5yh"
1. Keep ResumeDatabaseSection in App.tsx.
2. Add expandedResumeIds state.
3. Make each resume card header clickable.
4. Hide textarea/full skills/actions unless expanded.
5. Keep filename, version, current/enabled status visible always.
6. Add a short one-line skills preview in collapsed mode.
7. Add CSS for compact cards.
8. Test upload, save skills, enable/disable, delete, and page refresh.
```

In simple words:

**The resume database works. The cards are just too expanded. Make each resume card collapsed by default, show only a short summary, and reveal the full skills/actions only when the user clicks expand.**
