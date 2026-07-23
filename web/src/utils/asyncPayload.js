export async function applyPayloadAsReady(promise, apply, isCurrent = () => true) {
  const payload = await promise
  if (isCurrent()) apply(payload)
  return payload
}
