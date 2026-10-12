// Relay (`relay setup`): Relay running before every model request. `relay uninstall` removes this file.
import { execSync } from "node:child_process";

const COMMAND = __COMMAND__;

export const RelayStart = async () => ({
  "chat.params": async () => {
    try {
      execSync(COMMAND, { stdio: "ignore", shell: "/bin/sh", timeout: 30000 });
    } catch {}
  },
});
