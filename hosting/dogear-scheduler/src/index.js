// dogear-scheduler: fires DOGEAR's stage and promote workflows on time through
// GitHub workflow_dispatch (see dispatch.js). NOT DEPLOYED (gate G10).
import { workflowForCron, dispatchWorkflow } from "./dispatch.js";

export default {
  async scheduled(event, env) {
    const workflow = workflowForCron(event.cron);
    const scheduledTime = new Date(event.scheduledTime).toISOString();
    if (!workflow) {
      console.log(JSON.stringify({ level: "error", msg: "unmapped cron", cron: event.cron, scheduledTime }));
      return; // a cron we do not recognise never triggers anything
    }
    if (env.PUBLICATION_ENABLED !== "true") {
      console.log(JSON.stringify({ level: "info", msg: "publication not enabled; nothing dispatched", cron: event.cron }));
      return;
    }
    const result = await dispatchWorkflow(env, workflow);
    console.log(JSON.stringify({
      level: result.ok ? "info" : "error", msg: result.ok ? "workflow dispatched" : "workflow dispatch failed",
      cron: event.cron, workflow, status: result.status, scheduledTime, ...(result.ok ? {} : { detail: result.detail }),
    }));
    if (!result.ok) throw new Error(`dogear-scheduler: dispatch ${workflow} failed (HTTP ${result.status})`);
  },

  // No open publish trigger: the HTTP entry point only reports liveness.
  async fetch() {
    return new Response("dogear-scheduler: alive. Runs are started by Cron Triggers only.\n",
      { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
