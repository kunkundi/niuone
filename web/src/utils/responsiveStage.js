export function responsiveStageHeight({
  viewportBottom,
  stageTop,
  footerHeight = 0,
  bottomPadding = 0,
  mobile = false,
} = {}) {
  const minimum = mobile ? 236 : 220
  const maximum = mobile ? 520 : 840
  const available = Math.floor(
    Number(viewportBottom)
      - Number(stageTop)
      - Math.max(0, Number(footerHeight) || 0)
      - Math.max(0, Number(bottomPadding) || 0)
      - 2,
  )
  if (!Number.isFinite(available)) return minimum
  return Math.max(minimum, Math.min(maximum, available))
}
