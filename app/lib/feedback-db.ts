import { env } from "cloudflare:workers";

export function feedbackDb() { return env.DB; }
