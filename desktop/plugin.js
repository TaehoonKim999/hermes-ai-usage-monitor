import { useEffect, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'
import {
  Button,
  Popover,
  PopoverContent,
  PopoverTrigger,
  STATUSBAR_AREAS,
  fmtDateTime,
  relativeTime,
  useQuery,
  useQueryClient
} from '@hermes/plugin-sdk'

const ID = 'ai-usage-monitor'
const QUERY_KEY = [ID, 'usage']
const PROVIDER_ORDER = ['claude', 'codex', 'glm']
const SHORT_LABELS = { claude: 'Cl', codex: 'GPT', glm: 'GLM' }
const POLL_INTERVAL_MS = 300000
const COLD_START_RETRY_MS = 5000
const WARN_THRESHOLD = 80

function percentOf(window) {
  const value = window?.percent
  if (value == null || !Number.isFinite(value)) return null
  return Math.round(value)
}

function percentText(window) {
  const percent = percentOf(window)
  return percent == null ? '–' : `${percent}%`
}

function warnColor(window) {
  const percent = percentOf(window)
  if (percent == null) return undefined
  if (percent >= 100) return 'var(--ui-red)'
  if (percent >= WARN_THRESHOLD) return 'var(--ui-orange)'
  return undefined
}

function percentSpan(window, key) {
  return jsx('span', {
    key,
    style: { color: warnColor(window) },
    children: percentText(window)
  })
}

function providerSegment(providerId, provider) {
  const short = SHORT_LABELS[providerId] || provider?.label || providerId
  return jsxs('span', {
    key: providerId,
    style: { whiteSpace: 'nowrap' },
    children: [
      `${short} `,
      percentSpan(provider?.five_hour, `${providerId}-5h`),
      '/',
      percentSpan(provider?.seven_day, `${providerId}-7d`)
    ]
  })
}

function statusbarChildren(providers) {
  const children = []
  PROVIDER_ORDER.forEach((providerId, index) => {
    if (index > 0) children.push(' · ')
    children.push(providerSegment(providerId, providers?.[providerId]))
  })
  return children
}

function resetLabel(resetsAt) {
  if (!resetsAt) return ''
  const ms = Date.parse(resetsAt)
  if (Number.isNaN(ms)) return ''
  try {
    return relativeTime(ms)
  } catch {
    return ''
  }
}

function windowRow(label, window) {
  const reset = resetLabel(window?.resets_at)
  return jsxs('div', {
    style: { display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 },
    children: [
      jsxs('span', {
        children: [
          `${label} `,
          jsx('span', {
            style: {
              color: warnColor(window) || 'var(--ui-text-secondary)',
              fontWeight: 600
            },
            children: percentText(window)
          })
        ]
      }),
      reset
        ? jsx('span', {
            style: { color: 'var(--ui-text-quaternary)', whiteSpace: 'nowrap' },
            children: `resets ${reset}`
          })
        : null
    ]
  })
}

function providerCard(providerId, provider) {
  const label = provider?.label || SHORT_LABELS[providerId] || providerId
  return jsxs('div', {
    key: providerId,
    style: { display: 'flex', flexDirection: 'column', gap: 2 },
    children: [
      jsx('div', {
        style: { fontSize: '0.75rem', fontWeight: 600, color: 'var(--ui-text-primary)' },
        children: label
      }),
      windowRow('5h', provider?.five_hour),
      windowRow('7d', provider?.seven_day),
      provider?.error
        ? jsx('div', {
            style: { fontSize: '0.65rem', color: 'var(--ui-orange)' },
            children: provider.error
          })
        : null
    ]
  })
}

function UsageChip({ ctx }) {
  const [open, setOpen] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => ctx.rest('/usage'),
    refetchInterval: POLL_INTERVAL_MS,
    staleTime: 60000,
    retry: 1
  })

  const data = query?.data
  const providers = data?.providers
  const refetch = query?.refetch

  useEffect(() => {
    if (providers && Object.keys(providers).length > 0) return undefined
    const timer = setTimeout(() => refetch?.(), COLD_START_RETRY_MS)
    return () => clearTimeout(timer)
  }, [providers, refetch])

  const handleRefresh = async () => {
    if (refreshing) return
    setRefreshing(true)
    try {
      await ctx.rest('/refresh', { method: 'POST' })
    } catch {}
    try {
      await queryClient?.invalidateQueries?.({ queryKey: QUERY_KEY })
    } catch {}
    setRefreshing(false)
  }

  const hasProviders = Boolean(providers && Object.keys(providers).length > 0)

  const updatedAtMs = data?.updated_at ? Date.parse(data.updated_at) : NaN
  const updatedLabel = Number.isNaN(updatedAtMs)
    ? query?.isLoading
      ? 'Loading…'
      : 'No data yet'
    : `Updated ${fmtDateTime.format(updatedAtMs)}`

  const statusbar = hasProviders
    ? statusbarChildren(providers)
    : query?.isError
      ? 'AI usage –'
      : 'AI usage …'

  const body = jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', gap: 8, padding: 4, minWidth: 250 },
    children: [
      !hasProviders && query?.error
        ? jsx('div', {
            style: { fontSize: '0.7rem', color: 'var(--ui-red)' },
            children: String(query.error?.message || query.error || 'backend unreachable')
          })
        : null,
      ...PROVIDER_ORDER.map(providerId => providerCard(providerId, providers?.[providerId])),
      jsxs('div', {
        style: {
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginTop: 4,
          paddingTop: 8,
          borderTop: '1px solid var(--ui-stroke-secondary)'
        },
        children: [
          jsx('span', {
            style: { fontSize: '0.65rem', color: 'var(--ui-text-quaternary)' },
            children: updatedLabel
          }),
          jsx(Button, {
            variant: 'ghost',
            size: 'micro',
            onClick: () => void handleRefresh(),
            disabled: refreshing,
            children: refreshing ? 'Refreshing…' : 'Refresh'
          })
        ]
      })
    ]
  })

  return jsxs(Popover, {
    open,
    onOpenChange: setOpen,
    children: [
      jsx(PopoverTrigger, {
        asChild: true,
        children: jsx(Button, {
          variant: 'ghost',
          size: 'micro',
          style: { whiteSpace: 'nowrap' },
          'aria-label': 'AI usage: Claude, ChatGPT, GLM',
          title: 'AI usage: Claude · ChatGPT · GLM',
          children: statusbar
        })
      }),
      jsx(PopoverContent, { side: 'top', align: 'end', sideOffset: 6, children: body })
    ]
  })
}

export default {
  id: ID,
  name: 'AI Usage Monitor',
  description: 'Claude / ChatGPT (Codex) / GLM 5-hour and weekly usage in the statusbar.',
  defaultEnabled: false,
  register(ctx) {
    ctx.register({
      id: 'statusbar',
      area: STATUSBAR_AREAS.right,
      order: 150,
      render: () => jsx(UsageChip, { ctx })
    })
  }
}
