<!-- markdownlint-configure-file {"MD013": false} -->

# CodeJob — What It Actually Does

A plain-language description of everything this product does for the person using it.
No jargon, no internals — just the capabilities, and what each one is for in this
particular job search.

> **Companion document:** `docs/features.md` is the technical audit of the same
> ground — which routes exist and what evidence backs them. This file is the
> human version: what you can do, and why you would want to.

---

## Table of contents

- [The one-sentence version](#the-one-sentence-version)
- [The problem it was built for](#the-problem-it-was-built-for)
- [The main journey](#the-main-journey)
- [Capabilities in detail](#capabilities-in-detail)
  - [1. Bringing opportunities in](#1-bringing-opportunities-in)
  - [2. Deciding what is worth your time](#2-deciding-what-is-worth-your-time)
  - [3. The review desk](#3-the-review-desk)
  - [4. Fixing what could not be read](#4-fixing-what-could-not-be-read)
  - [5. Replying and sending](#5-replying-and-sending)
  - [6. Knowing who you are talking to](#6-knowing-who-you-are-talking-to)
  - [7. Tracking what you applied to](#7-tracking-what-you-applied-to)
  - [8. Managing your resumes](#8-managing-your-resumes)
  - [9. Writing and shaping a resume](#9-writing-and-shaping-a-resume)
  - [10. Reading replies](#10-reading-replies)
  - [11. The filed-mail workspace](#11-the-filed-mail-workspace)
  - [12. Asking questions in plain English](#12-asking-questions-in-plain-english)
  - [13. Spotting the same job twice](#13-spotting-the-same-job-twice)
  - [14. Standing reminders and digests](#14-standing-reminders-and-digests)
  - [15. Teaching it your vocabulary](#15-teaching-it-your-vocabulary)
  - [16. Searching everything](#16-searching-everything)
  - [17. Watching the machine work](#17-watching-the-machine-work)
  - [18. Your own numbers](#18-your-own-numbers)
  - [19. Controlling it from your phone](#19-controlling-it-from-your-phone)
  - [20. Tuning the whole thing](#20-tuning-the-whole-thing)
  - [21. More than one person](#21-more-than-one-person)
- [What is switched off right now](#what-is-switched-off-right-now)
- [What it deliberately will not do](#what-it-deliberately-will-not-do)

---

## The one-sentence version

It reads the recruiter mail arriving in your inbox, works out which messages are
real job opportunities worth your time, prepares a reply with the right resume
already attached, and waits for you to say yes.

---

## The problem it was built for

A contract job search in this market produces a particular kind of mess:

- Sixty recruiter emails land overnight. Most are mass blasts.
- The same job reaches you five times through five different agencies, and you
  cannot tell that from the subject lines.
- The one genuinely good role is sitting underneath forty that are not.
- Three weeks later a recruiter calls about "the role we discussed" and you have
  no memory of which role, which agency, or which version of your resume you sent.
- Two agencies submit you to the same client, which is the kind of collision that
  gets you quietly dropped by both.

Reading every message properly costs hours a day. Not reading them costs you the
good ones. This product exists to take the reading, sorting and preparing off your
plate, while leaving every decision that has a consequence with you.

**The principle running through all of it: the machine prepares, the human decides.**
Nothing is sent, applied to, or committed on your behalf without you clicking.

---

## The main journey

Here is the path a single recruiter email takes, start to finish:

1. **It arrives.** The system collects it from your mailbox.
2. **It gets read.** Role title, pay rate, required skills, location, seniority,
   the client behind it, and the sender's contact details are pulled out — and it
   records *where on the page* each of those came from, so you can check its work.
3. **Obvious rejections happen first.** If the role is in a state you have ruled
   out, or at a seniority you do not want, it is dropped immediately. These are
   your own rules, and they cost nothing to apply.
4. **A judgement call gets made.** For whatever survives, the question is asked:
   is this actually a job opportunity, or is it a newsletter, a networking blast,
   a "keep in touch" note, or a job board digest? This is the question that rules
   alone are bad at.
5. **It gets scored against you.** The role is compared to your own profile — your
   skills, your target roles, your rate — and given a fit score.
6. **It gets routed.** High enough score, it goes to your review desk. Too low, it
   is filed with a reason. Unreadable, it goes to a repair queue.
7. **A reply is drafted.** For anything reaching your desk, a response is written
   and the best-matching version of your resume is picked and attached.
8. **You decide.** Approve and send, reject, ask for a fresh draft, or fix the
   recipient if it guessed wrong.
9. **It becomes an application.** Approving creates the tracking record
   automatically — you never have to remember to log it.
10. **The reply comes back.** Responses are grouped by conversation and linked to
    the opportunity that started them.

Every stage leaves a trail. If something was rejected, you can see exactly which
rule or which score did it.

---

## Capabilities in detail

### 1. Bringing opportunities in

There are **three ways** a job opportunity can enter the system, and all three are
treated identically once they are inside — same reading, same filtering, same
scoring, same drafting.

**Your mailbox.** You connect your Google account once. From then on it can pull
in recruiter mail. You can point it at a single label rather than your whole
inbox, which is the recommended way to start: set up a mail filter that files
recruiter mail under a label, aim this at that label, and it never sees the rest
of your personal mail.

**An outside job feed.** A third-party source of postings can be pulled in on a
schedule, including a mode that tries to recover the real recruiter behind a
posting that hides the contact details.

**Pasting it in by hand.** Some requirements arrive on WhatsApp, over a phone call,
or through a channel nothing can read automatically. There is a box you can paste
raw text into, and it goes through the entire pipeline exactly as if it had arrived
by mail. The original text is kept word for word alongside whatever was extracted
from it, so you can always see what you actually pasted.

> **Use case here:** Most volume comes through the mailbox. The paste box is for
> the WhatsApp requirement a recruiter forwards you at 11pm — instead of it dying
> in a chat thread, it gets scored, matched to a resume, and lands on your desk
> with the others.

### 2. Deciding what is worth your time

Three separate filters run in sequence, cheapest first.

**Your hard rules.** Location, seniority, and policy limits you have set. These are
absolute and they run before anything expensive. A role in a state you will not
work in is gone immediately.

**The "is this real?" question.** The remaining messages get one judgement call:
is this an actual job opportunity? This catches the things rules cannot — the
"just building my network" note, the mass blast with six roles in it, the job
board digest, the "we'll keep you on file" reply. If this judgement is unavailable
or too slow, the system falls back to its own learned vocabulary and keeps working.
It never stalls waiting.

**The fit score.** What survives is compared against your profile and given a
number. You set the cutoff. Above it goes to your desk; below it is filed with the
reason attached.

There is also an optional stricter screening pass for when you want fewer, better
candidates reaching you.

> **Use case here:** Sixty emails overnight. Forty-one are mass blasts and
> duplicates. Twelve are out-of-state contract roles you ruled out months ago.
> Seven are real. You open the dashboard and see seven items — plus an entry
> explaining what happened to the other fifty-three.

### 3. The review desk

This is where you actually spend your time. Everything that passed the filters
waits here as a card.

Each card shows you:

- The role as it was understood — title, rate, skills, location, client.
- **Where each of those values came from.** The rate was in the message body; the
  location came from the subject line. You can see that, field by field.
- The fit score, and the breakdown behind it.
- Why it was routed here rather than filed.
- The drafted reply, and which resume was picked for it.
- Which resume won the match, what it matched on, what was missing, and how the
  runners-up scored.

Your actions on a card:

- **Approve and send** — the reply goes out with the resume attached.
- **Reject** — with a reason, kept on the record.
- **Regenerate** — throw away the draft and write a new one.
- **Fix recipients** — correct a wrong or missing address before sending.
- **Dismiss** — clear it without a formal rejection.
- **Track / untrack** — mark it as something you want followed.

You can also select many cards at once and act on the whole set. Double-clicking
a bulk action cannot send anything twice — the system recognises the repeat and
ignores it.

Every list in the product shares the same filtering and sorting behaviour:
suggestions that fill in as you type, the ability to hide filters you never use,
and a view that stays in the address bar so a filtered list is a link you can
bookmark or send to yourself.

> **Use case here:** A card reads "Java Developer, Austin TX, $65/hr". The rate
> came from the body, the location from the subject line — you can see both. You
> approve; the reply goes out with the Java resume attached; the application
> record is created without you touching anything else.

### 4. Fixing what could not be read

Some messages cannot be turned into a usable record — the formatting defeated the
reader, the role was never stated, the sender's address is unusable. Rather than
silently dropping them, they go to a separate repair queue.

There you can see the original, supply what was missing (most often the correct
address to reply to), and push it back into the review queue. Or delete it if it
was never worth anything.

> **Use case here:** When good opportunities seem to be missing, this queue is the
> first place to look. An empty review desk with a full repair queue means the
> problem is reading, not filtering.

### 5. Replying and sending

When a candidate reaches your desk, a reply is already written and a resume is
already chosen. You are editing and approving, not composing.

The resume choice is not random — every version you have uploaded is compared
against the role, and the best match wins. The full comparison is shown to you, so
you can see why that version and not another.

Optional: outbound mail can carry an open-tracking marker, so you know whether your
reply was opened.

There is a setting that would let replies go out automatically without review. It
is off, and the documentation is blunt about leaving it off unless you are certain.

### 6. Knowing who you are talking to

The same human being contacts you from three addresses across two agencies over
six months. Left alone, that is three unrelated contacts and no memory. This part
of the product reconciles them into one person.

It does this with evidence — shared phone numbers, matching email domains, name
similarity — and **every link it makes is recorded with the evidence behind it**.
Contacts carry a version history, so you can see how a record came to look the way
it does.

What you can do here:

- See one merged contact instead of three fragments.
- Resolve conflicts when two records disagree about the same person.
- Keep multiple addresses on one contact.
- Create contacts by hand for numbers you collected offline.
- Sort contacts into recruiter versus employer buckets.
- Recover deleted contacts from a recycle bin, and prune old versions.
- Generate a **cold-call script** from everything that contact has ever sent you.

> **Use case here:** An unknown number calls. You search it and find it belongs to
> a recruiter who emailed you twice in March about two different roles at the same
> end client — plus a script laying out exactly what you discussed. You take the
> call knowing more than they do.

### 7. Tracking what you applied to

*Currently switched off.*

Approving a candidate creates an application record, which then moves through a
lifecycle: submitted, right-to-represent signed, interview, outcome. Against each
one it tracks deadlines for your next action, risk notes, and how closely the role
resembles what you actually want.

The important piece is **duplicate submission detection**. If two agencies try to
put you in front of the same client, the collision is flagged before you sign the
second agreement.

It can also prepare follow-up messages for you. Those are written and held — never
sent on their own.

> **Use case here:** Two agencies both pitch you the same role at the same client.
> The collision is flagged before you sign the second right-to-represent. That is
> the difference between a clean submission and being dropped by both.

### 8. Managing your resumes

*Currently switched off.*

You keep several versions of your resume — one leaning Java, one leaning data, one
generic. This area keeps track of which version went where, and what happened next.

It also answers a forward-looking question. The original question — "which resume
version gets callbacks?" — turned out to be unanswerable honestly, because a
recruiter calling you back says nothing about whether your resume ever reached the
client. So instead it answers the question the evidence actually supports: **which
roles do you keep losing, and what would a winning resume for them need?** That is
built entirely from comparisons already made during scoring — nothing you have to
enter by hand.

> **Use case here:** A recruiter calls about a role from five weeks ago. You open
> the record and see exactly which of your four resume versions they are holding,
> so the conversation matches the document in front of them.

### 9. Writing and shaping a resume

There is a resume editor for producing a version in the shape a particular employer
asked for. You draft, and what the draft produces is a downloadable document. That
document only becomes one of your tracked resume versions when you upload it back
into your library — so there is never a mismatch between the file a recruiter
receives and the text the system reasons about.

Alongside it, the system can enrich what it knows about each resume version: the
primary role it targets, the skills it evidences, and a label to tell versions
apart at a glance. And it can explain, for any send, **why this resume** — what it
matched, what was missing, and how the alternatives scored.

### 10. Reading replies

*Currently switched off.*

Recruiter responses are grouped into conversations rather than arriving as loose
messages. Each conversation knows its direction — what you sent, what came back —
and links to the opportunity that started it. Replies worth acting on are surfaced
rather than left to be noticed.

> **Use case here:** Three recruiters replied while you were in an interview. The
> badge shows three, grouped into conversations, each linked back to the original
> opportunity. You answer all three without hunting through a mailbox.

### 11. The filed-mail workspace

*Currently switched off.*

A separate workspace for mail you have filed under a tracked label. It pulls
together the threads sitting in that label *and* related messages found elsewhere
in your mailbox — because a recruiter replying under a new subject line starts a
technically separate thread that is, to you, obviously the same conversation.

The workspace reassembles those into **one page per recruiter relationship**: what
they sent, what you sent, and what came back, in order.

The system can also apply labels to mail in your mailbox itself, with a preview so
you can see what it would do before it does it.

### 12. Asking questions in plain English

*Currently switched off. Needs a locally running assistant.*

A full-page assistant, plus a floating widget, that can answer questions about your
own pipeline in ordinary language.

It cannot roam freely through your records. It can only use a fixed set of
capabilities, and everything it sees is scoped to you. Within that, it can:

- Search your candidates and opportunities.
- Pull metrics and draw charts — trends, distributions, funnels, pipeline views.
- List your conversations.
- Rank opportunities for you.
- Render candidate tables with the source of each figure attached.
- Compare two records side by side.
- Read a file you attach to the conversation.
- Search the web, if you have given it a search service to use.

A second tier of capability, separately switched on, lets it **propose** things —
an email to send, an action on a candidate, an update to a record, a note to add.
Propose, not do. Each proposal waits for your confirmation.

Questions it is built to answer:

```text
Show me every candidate from last week scoring above 0.8
Which recruiters have I never replied to?
Chart my application funnel for the last 90 days
Compare record 412 and record 418
Draft a follow-up to the Austin Java role — don't send it
```

Conversations can be titled and exported.

### 13. Spotting the same job twice

*Currently switched off, and displaying the results requires a second switch.*

Five postings, three agencies, one actual job at one end client. This works out
which opportunities are really the same underlying role reaching you through
different vendors, so you pursue the strongest relationship instead of applying
five times and looking desperate to the client.

It runs deliberately in the background. It calculates and stores its conclusions
long before it is allowed to show them to you, because the honest position is that
**the signals it needs are too thin in the real data to trust yet** — the end client
is named in under one in fourteen opportunities, the prime vendor in none at all.
Showing it results it cannot support would be worse than showing nothing.

There is a labelling tool for building up a set of human-judged examples, and a
calibration harness to measure how good it actually is. Displaying results is
gated on it earning that right by measurement.

### 14. Standing reminders and digests

*Currently switched off.*

Recurring tasks that understand your timezone, in four kinds: a **reminder**, a
**digest**, a **monitor** that watches for a condition, and a **workflow** that
prepares multi-step work.

The rule governing all of them: **a task runs unattended right up to the point of
consequence, then stops and waits for you.** The dividing line is consequence, not
effort. A task may do an arbitrary amount of work preparing a result. Anything that
would leave the system waits for your click.

The safety behaviour is deliberate:

- A prepared item expires. An expired item cannot be approved even if the button is
  still on your screen — the clock is checked again at the moment you click.
- You get a warning partway through the window, before something expires unused.
- A task that fails three times in a row suspends itself rather than failing
  forever.
- Clock changes are handled properly: a skipped hour shifts forward, a repeated
  hour runs once.

Prepared work waits for you in its own review area, so nothing scheduled can exist
that you cannot see.

> **Use case here:** "Every Monday at 9am, show me applications with no reply for
> five or more days." Monday arrives, the digest is prepared and waiting. Nothing
> was sent.

### 15. Teaching it your vocabulary

The system learns the language of your particular market, but it does not adopt
anything without your approval.

When it encounters a skill, a company, a client, or a phrase it does not recognise,
it puts it in a pending list. You review those and approve or dismiss them, one at
a time or in bulk. Approved terms become part of how it reads future messages.

The same applies to the signals it uses to tell a real opportunity from a blast —
you can see what it has learned, approve or dismiss each signal, and flip one from
"this means yes" to "this means no" if it learned the wrong lesson.

There is a bulk review overlay for working through a backlog quickly, an import
path for bringing in terms in bulk, and a measurement view showing how well the
vocabulary is covering what actually arrives.

> **Use case here:** Your market has client names and stack abbreviations that
> mean nothing generically. Rather than a generic reader mangling them forever,
> you approve them once and every future message is read better.

### 16. Searching everything

A single search that looks across your whole pipeline at once and tells you which
area each result is in — review desk, repair queue, sent items, contacts, inbox, or
run history. You click a result and land on it.

Searches you run often can be **saved into a bucket** and recalled from a dropdown,
so a complicated filtered view is one click away rather than something you rebuild
each time.

### 17. Watching the machine work

A run history showing every pass the system has made: when it ran, what it looked
at, what it took, and — critically — **what it skipped and why**.

The skipped-item drill-down is the honesty mechanism. When you suspect something
good was thrown away, this is where you find out whether it was a hard rule, the
"is this real?" judgement, or the fit score that did it, and then adjust the right
setting rather than guessing.

Alongside it, a live view of work currently queued or running, with the ability to
start a pass on demand or cancel one in flight.

### 18. Your own numbers

The system records what you actually look at and act on, and turns it into trends
over time. Charts cover your pipeline shape, your funnel, distributions across
roles and rates, and your own activity.

This is about noticing patterns in your search: which weeks you went quiet, which
kinds of roles you keep opening and never replying to, whether your review backlog
is growing or shrinking.

### 19. Controlling it from your phone

*Requires setting up a bot.*

A chat bot that lets you review and act on your queue from your phone. Access is
restricted to specific chat accounts, and taking an action requires a PIN that
expires. This is the one surface intended to be reachable from outside your own
network, and it is gated accordingly.

> **Use case here:** You are out. A good role lands. You approve and send from
> your phone rather than letting it sit until evening.

### 20. Tuning the whole thing

A settings area covering:

- **Your profile** — skills, target roles, seniority, rate expectations, the
  locations you will and will not work in. You can build this by hand, add entries
  one at a time, or have it read from a document you upload.
- **Your resumes and attachments** — upload versions, label them, describe the role
  each targets, and manage what gets attached to replies.
- **Supporting documents** — a library of other documents you may need to send.
- **The fit cutoff** — the score above which something reaches your desk.
- **Automatic collection** — whether to check your mailbox on a schedule, and how
  often.
- **Trusted sender groups** — mail from senders you have marked as trusted can be
  treated differently from cold blasts.
- **Mailbox connection** — connect, check status, disconnect, and restrict to a
  single label.
- **Which capabilities are on** — most of the larger areas described here can be
  turned on and off, and the ones that only need a setting change take effect
  immediately.

When too much noise reaches you, the guidance is: raise the cutoff, turn on
stricter screening, tighten your location and seniority rules. When too little
reaches you, lower the cutoff and check the repair queue and the skipped-item log
first — the problem is often reading, not filtering.

### 21. More than one person

*Currently switched off. The system runs as a single person's tool by default.*

There is a sign-in capability built in. It works through your Google account —
there is no signup form and no password anywhere in the system, by design. Whether
you can get in is decided by Google's own approved-user list, so there is only ever
one list to maintain rather than two that drift apart.

When it is on:

- Each person's data is entirely separate from everyone else's. One person cannot
  see another's opportunities, contacts, applications, or conversations.
- Each person connects their own mailbox, and their own assistant credentials.
  Nobody's mailbox access is shared.
- Signed-in sessions are tracked, so "log out everywhere" and "cut this account off
  right now" both actually work immediately, rather than waiting for something to
  expire.
- There is an administrator view for listing people and disabling an account.
  Administrator status can only be granted from the server side — never through a
  request, never claimed by the person signing in.

> **Note:** The main project README still describes the product as single-owner
> with no sign-in. That was true, and the sign-in capability described here has
> since been built behind a switch that is off by default. Both statements
> describe the same system: as shipped and unconfigured, it is a single person's
> tool.

---

## What is switched off right now

Most of the larger capabilities are off by default. This is deliberate, and the
reasons differ:

| Area | Why it is off |
| --- | --- |
| Application tracking | Ready; not turned on |
| Resume tracking | Ready; not turned on |
| Reply inbox | Ready; not turned on |
| Filed-mail workspace and labelling | Ready; not turned on |
| The assistant | Needs a locally running assistant to talk to |
| Assistant proposals | Second switch, on top of the assistant itself |
| Same-job detection | **Held back on purpose** — the real data is too thin to trust it yet |
| Showing same-job results | Held back until it proves itself by measurement |
| Scheduled tasks and digests | **Held back on purpose** — the shipped default conditions were tested against real data and two of the three can never fire |
| Automatic sending | Off, and recommended to stay off |
| Automatic collection on a schedule | Off; you run a pass when you want one |
| Multiple people signing in | Off; runs as a single person's tool |
| Outside job feed | **On** — this is the one default-on integration |

The two marked "held back on purpose" are the interesting ones. Both are fully
built. Both are switched off because someone checked them against real data and
found they would not do what they promise yet. That is the intended behaviour of
this product: a capability earns the right to be shown to you.

---

## What it deliberately will not do

- **It will not send anything you did not approve.** Every path that would put
  something in front of another human ends at a button you press.
- **It will not hide its reasoning.** Every rejection has a reason you can read.
  Every extracted value says where it came from. Every merged contact shows the
  evidence behind the merge. Every resume choice shows the comparison.
- **It will not pretend to know things it does not.** Where the data cannot support
  a conclusion, the feature stays off rather than showing you a confident guess.
- **It will not act while you are not looking.** Scheduled work prepares and stops.
  The assistant proposes and stops. Nothing crosses the line into consequence on
  its own.
