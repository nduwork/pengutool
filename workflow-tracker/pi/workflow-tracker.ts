// workflow-tracker for pi: what the Claude Code hooks and status line do, as one pi extension.
// Installed by `make install-tracker` into ~/.pi/agent/extensions/workflow-tracker.ts.
//
//  - before each prompt, hook_prompt.sh's line (the current chain, or the nudge to set one) is appended
//    to the system prompt, like the UserPromptSubmit/SessionStart hooks
//  - the chain is shown in the footer, like the status line, refreshed after every bash call
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"
import { execFile } from "node:child_process"
import * as path from "node:path"

const BIN = process.env.STEP_STATUS_BIN || "__STEP_STATUS_BIN__"  // baked by make install-tracker
const sh = (script: string, args: string[], cwd: string, input = "") => new Promise<string>((resolve) => {
  const child = execFile("bash", [path.join(BIN, script), ...args], { cwd, timeout: 10000 },
    (e, out) => resolve(e ? "" : String(out).trim()))  // the tracker is optional: never break the turn
  child.stdin?.end(input)
})

export default function (pi: ExtensionAPI) {
  const show = async (ctx: any) => {
    if (ctx.hasUI) ctx.ui.setStatus("workflow-tracker", (await sh("steps.sh", ["render"], ctx.cwd)) || undefined)
  }
  pi.on("session_start", async (_event, ctx) => show(ctx))
  pi.on("tool_result", async (event: any, ctx) => { if (event.toolName === "bash") await show(ctx) })
  pi.on("before_agent_start", async (event, ctx) => {
    const line = await sh("hook_prompt.sh", [], ctx.cwd, JSON.stringify({ cwd: ctx.cwd }))
    if (line) return { systemPrompt: `${event.systemPrompt}\n\n${line}` }
  })
}
