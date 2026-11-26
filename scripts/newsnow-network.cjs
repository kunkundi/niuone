"use strict";

// Cross-region sources can need more than Node's 250 ms connection attempt.
// Keep IPv4/IPv6 fallback while allowing each address enough time to connect.
const net = require("node:net");
if (typeof net.setDefaultAutoSelectFamilyAttemptTimeout === "function") {
  const current = net.getDefaultAutoSelectFamilyAttemptTimeout();
  net.setDefaultAutoSelectFamilyAttemptTimeout(Math.max(current, 2000));
}
