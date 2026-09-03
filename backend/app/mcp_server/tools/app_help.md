# CodeJob app help

Short how-to notes for the chat assistant to answer navigation questions
accurately instead of guessing. Add a new "## Topic" section per workflow.

## Resume upload

Location: Settings page -> Resumes section.
Click "Upload New Resume" and pick a PDF or DOCX file. The upload becomes
your active (current) resume unless you mark a different one current.
You can enable/disable or delete existing resumes from the same list.
Endpoint: POST /settings/resume.

## Reviewing and sending a candidate reply

Location: Candidates page -> Needs Review tab.
Open a candidate card to see the generated draft reply, then Approve & Send
or Reject. Cards with no ready draft show why under "draft status".

## Premium Numbers

Location: Premium Numbers page. Sub-tabs: Number Review, Recruiter Numbers,
Employer Numbers, Recruiter Opportunities. Unclassified phone numbers land
in Number Review first; marking one as recruiter/employer moves it to the
matching tab.

## Chat assistant

This chat can look up your candidates, runs, inbox conversations, resumes,
contacts, opportunities, AI health, and settings. It can also prepare actions
- sending a reply, approving candidates, creating a contact, filing an issue -
but it never performs them itself: it shows a confirmation card, and only your
click executes the action. You can attach .pdf, .docx, .txt, and .csv files on
the Assistant page; the floating widget is read-and-prepare only.

## CodeJob Assistant page

Location: sidebar -> Assistant.
The full-page workspace for the same conversation the floating widget shows -
both surfaces share one session, so a thread started in either continues in the
other. The page adds file attachments (drag a file onto the composer or use the
attach button), a conversation list grouped by day, and room for tables and
charts the widget is too narrow to draw.
