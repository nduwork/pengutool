# PenguPool demo: the movie script

This is the script behind the animated tutorial on the landing page, and the running order for a live
demo. Each scene is one chapter of the player and one GIF below (`docs/assets/tutorial/`, rendered from the player by
`scripts/record_tutorial.py`). The demo project
and every name in it are fictional.

**The story:** you add token refresh to a small web app, `shop-app`. A `lead` session plans the work, an
`api` session builds the endpoint in its own worktree, a `ui` session wires up the form, and a `reviewer`
checks it, all from one Cursor window.

**Cast:** `lead` (Claude Code, root) · `api` (Claude Code, worktree `shop-app-wt-api`) · `ui` and
`reviewer` (Claude Code, same folder as `lead`) · `docs-writer` (pi).

**Running time:** about 90 seconds for the loop, 5–7 minutes live.

---

## Scene 1 · Install (≈10 s)

![Scene 1: Install](assets/tutorial/1-install.gif)

| | |
| --- | --- |
| **Shot** | Cursor with an empty PenguPool sidebar ("No sessions yet"). The bottom panel is a plain `zsh` terminal. |
| **Action** | Type `curl -fsSL https://pengupool.nduwork.com/install.sh \| bash`. The installer reports the CLI, Claude Code hooks, pi extension, workflow tracker, and "Extension installed in Cursor". |
| **Caption** | One command installs the backend, the harness wiring and the editor extension. |
| **Live demo** | Run it for real beforehand; on stage, show the output and reload the window. Nothing to configure. |

## Scene 2 · Start a pool (≈16 s)

![Scene 2: Start a pool](assets/tutorial/2-start-a-pool.gif)

| | |
| --- | --- |
| **Shot** | Sidebar focused. |
| **Action** | Press `n`. Name the session `lead`, choose **Use selected folder**, choose **Claude Code**. `lead` appears as ● Active in the sidebar and as a card on the map. Repeat quickly: `api` with **Create a worktree** (its repo shows `shop-app-wt-api`), `ui` and `reviewer` in the same folder, and `docs-writer` with **pi** (it lands under *Pi Sessions*). |
| **Caption** | Press n for each session: pick the folder or a new worktree, and the harness. |
| **Live demo** | Say why `api` gets a worktree (it edits the same files as `ui`) and `reviewer` doesn't (it only reads). |

## Scene 3 · Group the children (≈10 s)

![Scene 3: Group the children](assets/tutorial/3-group-the-children.gif)

| | |
| --- | --- |
| **Shot** | Four top-level Claude sessions on the map. |
| **Action** | Drag `api` onto `lead`, then `ui`, then `reviewer`. Rows indent under `lead`; the map redraws into a tree. |
| **Caption** | Drag each child onto its parent. The map becomes the tree every session sees. |
| **Live demo** | Mention `g` as the keyboard way, and that a Claude session can't be grouped under a pi one. |

## Scene 4 · Brief the parent (≈14 s)

![Scene 4: Brief the parent](assets/tutorial/4-brief-the-parent.gif)

| | |
| --- | --- |
| **Shot** | The PenguPool terminal on `lead`. |
| **Action** | Type: *"I added api, ui and reviewer under you. api owns the REST endpoints in server/, ui the login form, reviewer reviews diffs. Route work to them and keep planning yourself."* `lead` answers `Triage: mine` and confirms the plan. Hover `api`: the tooltip shows its role, which `api` set itself when it was told `ROLE REQUIRED`. |
| **Caption** | Tell the parent about every new child and how you plan to use it. |
| **Live demo** | This is the habit to sell. Without it the parent sees names but not intent. Right-click → *Describe Role…* to edit a role. |

## Scene 5 · Ask the top, watch it route (≈16 s)

![Scene 5: Ask the top, watch it route](assets/tutorial/5-ask-the-top.gif)

| | |
| --- | --- |
| **Shot** | Map and Log side by side, terminal on `lead`. |
| **Action** | Type *"Add token refresh to login."* `lead` replies `Triage: → api, ui` and sends two messages. The Log shows green `lead ⇢ api` and `lead ⇢ ui`; the `api` and `ui` cards turn Active; the workflow chain appears: `[ship-auth] api ● → ui ○ → review ○`. `api` finishes: blue `api ⇢ lead: endpoint done, tests pass`, and the chain moves to `api ✓ → ui ●`. |
| **Caption** | Talk to the top. Triage routes each part to the child that owns it. |
| **Live demo** | Point at the `Triage:` line. It's how you see the decision before any work happens. |

## Scene 6 · Direct line and approvals (≈14 s)

![Scene 6: Direct line and approvals](assets/tutorial/6-direct-line.gif)

| | |
| --- | --- |
| **Shot** | Terminal switched to `ui`. |
| **Action** | Type *"@reviewer check my form diff."* An orange `ui ⇢ reviewer` message appears: the tag opened a direct line for this prompt. `reviewer` needs to run tests: its card turns **? Approval**. Click it; the terminal shows the permission prompt; approve. The card goes back to ● Active and the chain reaches `review ●`. |
| **Caption** | Tag @session for a one-off direct line. Approval badges show who is waiting on you. |
| **Live demo** | Stress that the tag lasts one prompt; for anything lasting, regroup. |

## Scene 7 · Keep the tree healthy (≈12 s)

![Scene 7: Keep the tree healthy](assets/tutorial/7-keep-it-healthy.gif)

| | |
| --- | --- |
| **Shot** | Sidebar. |
| **Action** | Right-click `api` → **Restart & Resume (Shift+R)**. A "restarting api…" notification; the card blinks and comes back ● Active in the same terminal, same group. Then `lead`'s context badge turns red (71%): press `c` on it to compact, and it drops back to green (9%). The chain completes: `api ✓ → ui ✓ → review ✓`. |
| **Caption** | Restart in place after an update. Compact with c, never /new or /clear. |
| **Live demo** | End on the finished chain and the full tree. |

---

## Presenter checklist

- A fictional repo (`shop-app`) with a `server/` and a `web/` folder; no real names on screen.
- Cursor with only the PenguPool view open: Map and Log side by side, terminal panel at the bottom.
- Create the sessions live (scenes 2–3). The briefing in scene 4 is the point of the talk.
- Keep prompts short and on screen long enough to read. Pause on each `Triage:` line.
- Have a small, fast task ready for `api` so scene 5 finishes on stage.
