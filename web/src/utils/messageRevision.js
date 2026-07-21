export function messagePageRevision(payload, category) {
  const categoryData = payload?.categories?.[category] || {}
  const latest = payload?.records?.[0] || {}
  return revisionKey({
    category,
    count: Number(categoryData.count || 0),
    latest,
  })
}

export function revisionKey(revision) {
  return JSON.stringify({
    category: String(revision?.category || ''),
    count: Number(revision?.count || 0),
    latest: {
      id: String(revision?.latest?.id || ''),
      timestamp: revision?.latest?.timestamp ?? null,
      content_hash: String(revision?.latest?.content_hash || ''),
      updated_at: String(revision?.latest?.updated_at || ''),
    },
  })
}
