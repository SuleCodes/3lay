/**
 * 3lay inbound email Worker (Cloudflare Email Routing).
 *
 * Every email routed to this Worker is first checked against the backend:
 * mail for an address that isn't a registered client's forwarding address
 * ({username}@in.3lay.live) is bounced. Accepted mail is forwarded, as the
 * raw message (.eml), to the Azure ingest function, which stores it at
 * `<client>/<origin>/YYYY/MM/DD/<id>`:
 *
 *   X-3lay-Client  who the email was sent to (the address it was routed for)
 *   X-3lay-Origin  who sent it (the From header's address)
 *
 * Only this Worker holds the function key and the API key, which is what
 * lets the function trust those two headers.
 *
 * Bindings (see wrangler.toml):
 *   FUNCTION_URL    var     Base URL of the Function App, e.g. https://func-3lay.azurewebsites.net
 *   FUNCTION_KEY    secret  Azure function key for /api/ingest (`wrangler secret put FUNCTION_KEY`)
 *   INGEST_API_KEY  secret  The Worker's one shared key, sent to both the
 *                           function (X-3lay-Api-Key, must match
 *                           FUNCTION:API_KEY) and the backend
 *                           (X-3lay-Internal-Key, must match
 *                           BACKEND:INTERNAL_API_KEY) -- the same value in
 *                           all three places (`wrangler secret put INGEST_API_KEY`)
 *   BACKEND_URL     var     The 3lay API, e.g. https://api.3lay.live
 */

export default {
  async email(message, env) {
    await handleEmail(message, env);
  },
};

/**
 * Exported separately so it can be called from an existing Worker's email()
 * handler: `await handleEmail(message, env);`
 */
export async function handleEmail(message, env) {
  if (!env.INGEST_API_KEY) {
    // Misconfiguration, not the sender's fault -- fail without bouncing.
    throw new Error("INGEST_API_KEY secret is not set");
  }

  // message.to is the envelope recipient: the address this copy of the email
  // was actually delivered to. Unlike the To header, that's correct for CC
  // and BCC recipients too.
  const client = message.to.toLowerCase();
  const origin = senderAddress(message);

  // Only registered clients' addresses are accepted. Anything else is bounced
  // here, before it costs a function call or a byte of storage.
  if (!(await isKnownRecipient(client, env))) {
    console.log(`Rejected ${origin} -> ${client}: not a client address`);
    message.setReject("Address not found");
    return;
  }

  // Email Routing caps messages at 25 MiB, so buffering is safe -- and the
  // function needs a Content-Length anyway (the Azure Functions Python host
  // drops chunked request bodies).
  const raw = await new Response(message.raw).arrayBuffer();

  let upstream;
  try {
    upstream = await fetch(`${env.FUNCTION_URL.replace(/\/+$/, "")}/api/ingest`, {
      method: "POST",
      headers: {
        "X-3lay-Client": client,
        "X-3lay-Origin": origin,
        "x-functions-key": env.FUNCTION_KEY,
        "X-3lay-Api-Key": env.INGEST_API_KEY,
        "Content-Type": "message/rfc822",
      },
      body: raw,
    });
  } catch (err) {
    // Transient: don't bounce. Throwing hands the failure back to Email
    // Routing rather than sending the sender a permanent rejection.
    throw new Error(`Ingest function unreachable: ${err}`);
  }

  if (upstream.status === 202) {
    const { id } = await upstream.json();
    console.log(`Ingested ${id}: ${origin} -> ${client} (${raw.byteLength} bytes)`);
    return;
  }

  const detail = await upstream.text();
  if (upstream.status === 400) {
    // Permanent: an address the function won't accept as a path segment
    // (e.g. unusual characters). Retrying won't help, so bounce it.
    console.error(`Rejected ${origin} -> ${client}: ${detail}`);
    message.setReject("Message could not be accepted for processing");
    return;
  }

  // 401/403 (our function key or API key is wrong) or 5xx: our problem, not the
  // sender's, so treat as transient rather than bouncing.
  throw new Error(`Ingest function returned ${upstream.status}: ${detail}`);
}

/**
 * Asks the backend whether `address` is a client's forwarding address
 * ({username}@in.3lay.live). True for 200, false for 404. Anything else --
 * backend down, slow cold start past the timeout, wrong key -- throws, so a
 * problem on our side never bounces a real client's mail.
 */
async function isKnownRecipient(address, env) {
  const url = `${env.BACKEND_URL.replace(/\/+$/, "")}/internal/recipients/${encodeURIComponent(address)}`;

  let res;
  try {
    res = await fetch(url, {
      headers: { "X-3lay-Internal-Key": env.INGEST_API_KEY },
      // Generous: the backend scales to zero, so the first request after an
      // idle spell includes its cold start.
      signal: AbortSignal.timeout(30_000),
    });
  } catch (err) {
    throw new Error(`Recipient check failed (backend unreachable): ${err}`);
  }

  if (res.status === 200) return true;

  const body = await res.text();
  // Only the endpoint's own answer means "not a client". A bare 404 (e.g. an
  // older backend without this endpoint, or a wrong BACKEND_URL) must not be
  // read as "unknown recipient" -- that would bounce every client's mail.
  if (res.status === 404 && body.includes('"Unknown recipient"')) return false;
  throw new Error(`Recipient check failed: backend returned ${res.status}: ${body}`);
}

/**
 * The sender's address from the From header, e.g. `"Acme, Inc" <ap@acme.com>`
 * -> `ap@acme.com`. Falls back to the envelope sender (message.from), which
 * for newsletters and forwarded mail is often a bounce address rather than
 * the person or company that sent it.
 */
function senderAddress(message) {
  const from = message.headers.get("From") || "";
  const match = from.match(/<([^<>\s]+@[^<>\s]+)>\s*$/) || from.match(/([^\s<>"',;]+@[^\s<>"',;]+)/);
  return (match ? match[1] : message.from).toLowerCase();
}
