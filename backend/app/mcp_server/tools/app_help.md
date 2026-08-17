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

This chat is read-only: it can look up your candidates, runs, inbox
conversations, resumes, AI health, and settings, but it cannot upload files,
send emails, or change settings for you. Those actions require the UI.
