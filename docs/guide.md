# Working with a pool

PenguPool enforces the session tree: who may message whom, when a request must be routed, and when a
session must describe itself. It can't know what you meant a session *for*. These practices close that gap.

## Set up a pool in five minutes

1. **Start the root.** Press `n` in the Sessions view, pick the project folder and a harness, and name it
   after its job (`lead`, not `shop-app`).
2. **Add the children.** Press `n` again for each child. Choose **Use selected folder** or **Create a
   worktree** (see [Folder or worktree](#pick-folder-or-worktree-on-purpose)).
3. **Group them.** Press `g` on a child or drag it onto its parent. A session can only be grouped under a
   session of the same harness (Claude Code or pi).
4. **Brief the parent.** Tell it which children it now has and what each one is for. Copy the parent
   template below.
5. **Brief each child** with the child template.
6. **Check the roles.** Hover a row in the Sessions view: the tooltip shows the role, or *role not set*.
   A child with no role is asked to describe itself on its next prompt.

## The practices

### 1. Tell the parent about every new child

A new child only shows up in the parent's tree block, with no explanation. Tell the parent, in its own
terminal, that you added the child and how you plan to use it. The parent then knows what to route and
what to keep.

Parent template:

```text
I added <child> under you. It owns <what: area, directory, or kind of task>.
Route <which requests> to it and keep <what stays with you> yourself.
When it reports back, <what you want the parent to do: review, merge, summarise>.
```

Example:

```text
I added api under you. It owns the REST endpoints in server/. Route API work to it and keep planning
and review yourself. When it reports back, review the diff before telling me it's done.
```

Child template:

```text
You are <child>, under <parent>. You own <area>. Set your role with pengupool ctl describe
(summary, responsibility and routing keywords), then wait for work from <parent>.
Report results back to <parent>, not to me.
```

### 2. Give every session a role and keywords

```sh
pengupool ctl describe <session-id> \
  --summary "REST API" \
  --responsibility "Own the server/ endpoints, their tests and the API docs" \
  --keywords "api, endpoint, auth, server"
```

- The **summary** is the one-line role other sessions see in the tree.
- **Keywords** drive triage. When your prompt to a parent matches a child's keywords, name, workspace or
  role, the parent is told `ROUTE REQUIRED` and must message that child first. If the turn ends
  without that message, the Stop hook sends it back once.
- A session with no role is told `ROLE REQUIRED` and must describe itself before it does anything else.
  A parent can also set a child's role (the child is told who changed it), and so can you: right-click a
  row → **Describe Role…**.

Pick keywords that belong to this session and not to its parent. A term that also describes the parent
doesn't count.

### 3. Talk to the top, let triage route

Send requests to the root (or the lowest session that owns the whole request) and let it split the
work. Each grouped reply starts with a `Triage:` line (`Triage: mine`, `Triage: → api`,
`Triage: asked parent`), so you can see the decision before any work happens.

To keep a task where it is, say so in the prompt: "do it yourself" or "don't delegate" turns routing
off for that prompt.

### 4. Use @session for a one-off direct line

Messages normally go one edge at a time: parent ↔ direct children. For a one-off exception, tag the
session in your prompt:

```text
@reviewer check the diff in api before I merge.
```

The session you typed into may message `reviewer` directly, and `reviewer` may reply, until your next
prompt. If a name is ambiguous, nothing is granted; tag the session id instead (`@<id>`, 8+ characters).
In the Log, these messages are orange.

### 5. Pick folder or worktree on purpose

| Use the selected folder when… | Create a worktree when… |
| --- | --- |
| the child reviews, reads, researches or writes docs | two sessions will edit the same files |
| you want a second pair of hands on the same branch | the child's work should land as its own branch |
| another session already runs there (that's fine) | you may throw the work away |

Worktrees are created next to the repo as `<repo>-wt-<name>` on a `pengupool/<name>` branch.

### 6. Keep the tree intact

- Compact with `c` in the Sessions view, or `/compact` in the session. **Don't use `/new` or `/clear`.**
  Either starts a new session, and the new session isn't in the group.
- After a Claude Code or pi update, press `Shift+R` (or right-click → **Restart & Resume**). The session
  stops and resumes in the same terminal with the new version, keeping its id, group and role.
- To reorganise, drag rows. Dropping a row on empty space moves it to the top level.

## How routing works

- A grouped session may message only its **parent** and its **direct children**. The guard refuses any
  other send before delivery and names the valid recipients.
- To reach a session further away, a session sends the request one edge toward it.
  `pengupool ctl route <id> <target>` prints the next hop. Replies travel back along the same path.
- A child never hands work to its parent. When it gets something it doesn't own, it asks the parent who
  should handle it.
- Sessions that aren't grouped, replies, and names that aren't live sessions are left alone.

## Troubleshooting

| You see | It means |
| --- | --- |
| `○ Stale` on a card | The session has been idle for more than 10 minutes. It's still running; this isn't an error. A session busy in a long turn stays Active. |
| `? Approval` | The session is waiting for you to approve a tool call. |
| "routing skipped for `<name>`" | A prompt matched a child, but the session answered it itself. It gets sent back once to route it. |
| A message was refused | The recipient isn't adjacent. The refusal names the next hop. |
| A session dropped out of its group | It ran `/new` or `/clear`. Press `a` (Add Previous Session) to resume the old session; groups are kept by session id, so it returns to its place. |
| Roles, tree or hooks look out of date | Reload the editor window. Restart any Claude session that predates an install (`Shift+R`). |
