export const INDUSTRY_FLOW_EMPTY_RETRY_DELAYS_MS = Object.freeze([1000, 2500, 5000])

export function hasIndustryFlowNodes(payload) {
  return Array.isArray(payload?.nodes) && payload.nodes.length > 0
}

export function hasIndustryMoneyFlowRows(payload) {
  const moneyFlow = payload?.money_flow
  return Boolean(
    (Array.isArray(moneyFlow?.inflow) && moneyFlow.inflow.length)
    || (Array.isArray(moneyFlow?.outflow) && moneyFlow.outflow.length),
  )
}

export function mergeIndustryFlowPayload(current, incoming) {
  const previous = current && typeof current === 'object' ? current : {}
  const next = incoming && typeof incoming === 'object' ? incoming : {}
  if (hasIndustryFlowNodes(next)) {
    return {
      payload: { ...next, loading: false, loaded: true },
      receivedData: true,
      preservedData: false,
    }
  }

  const error = next.error || '行业资金流暂未返回有效数据，正在重试'
  if (hasIndustryFlowNodes(previous)) {
    return {
      payload: {
        ...previous,
        loading: false,
        loaded: true,
        stale_cache: true,
        error,
      },
      receivedData: false,
      preservedData: true,
    }
  }
  return {
    payload: { ...next, loading: false, loaded: true, error },
    receivedData: false,
    preservedData: false,
  }
}
