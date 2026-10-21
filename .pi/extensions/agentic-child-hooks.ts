// Child-session entry point for the shared Pi lifecycle adapter.
//
// Child roles keep sensitive/destructive guards and post-edit hooks, but they
// must not run parent completion enforcement: test-first intentionally settles
// with a red gate after writing failing tests.

import { registerAgenticHooks } from "./agentic-hooks.ts";

export default function agenticChildHooks(pi) {
  registerAgenticHooks(pi, { parentLifecycle: false });
}
