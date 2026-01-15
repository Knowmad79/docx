import { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type ZoneType = 'STAT' | 'TODAY' | 'THIS_WEEK' | 'LATER'

interface GridItem {
  id: string
  subject: string
  snippet: string | null
  risk_score: number
  lifecycle_state: string
  deadline_at?: string | null
  patient_name?: string | null
}

interface GridZone {
  zone: ZoneType
  total_count: number
  overdue_count: number
  items: GridItem[]
}

interface GridResponse {
  owner?: string | null
  zones: GridZone[]
}

interface GridViewProps {
  apiCall: (endpoint: string, options?: RequestInit) => Promise<any>
  owner?: string
  onNotify?: (message: string) => void
}

const zoneConfig: Record<ZoneType, { label: string; color: string; badge: string }> = {
  STAT: { label: 'STAT', color: 'text-red-400', badge: 'bg-red-500/20 text-red-400 border-red-500/30' },
  TODAY: { label: 'TODAY', color: 'text-orange-400', badge: 'bg-orange-500/20 text-orange-400 border-orange-500/30' },
  THIS_WEEK: { label: 'THIS_WEEK', color: 'text-blue-400', badge: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
  LATER: { label: 'LATER', color: 'text-zinc-400', badge: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30' },
}

export default function GridView({ apiCall, owner, onNotify }: GridViewProps) {
  const [grid, setGrid] = useState<GridResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [selectedItem, setSelectedItem] = useState<GridItem | null>(null)

  const fetchGrid = async () => {
    setLoading(true)
    try {
      const params = owner ? `?owner=${encodeURIComponent(owner)}` : ''
      const data = await apiCall(`/api/state/grid${params}`)
      if (data) setGrid(data)
    } catch (error) {
      console.error('Failed to fetch grid:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchGrid()
  }, [owner])

  const handleEscalate = async () => {
    if (!selectedItem) return
    try {
      await apiCall(`/api/messages/${selectedItem.id}/escalate`, { method: 'POST' })
      onNotify?.('Escalated to lead doctor.')
      setSelectedItem(null)
      fetchGrid()
    } catch (error) {
      onNotify?.(error instanceof Error ? error.message : 'Escalation failed')
    }
  }

  const zones = grid?.zones || []

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-zinc-100">Decision Deck</h2>
        <Button variant="outline" size="sm" onClick={fetchGrid} disabled={loading} className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700">
          {loading ? 'Refreshing...' : 'Refresh'}
        </Button>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {(['STAT', 'TODAY', 'THIS_WEEK', 'LATER'] as ZoneType[]).map((zone) => {
          const zoneData = zones.find((z) => z.zone === zone)
          const items = zoneData?.items || []
          const preview = zone === 'STAT' ? items.slice(0, 3) : items.slice(0, 8)
          return (
            <Card key={zone} className="bg-zinc-900 border-zinc-800">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className={`text-sm font-semibold ${zoneConfig[zone].color}`}>{zoneConfig[zone].label}</CardTitle>
                  <div className="flex items-center gap-1">
                    <Badge className={`border ${zoneConfig[zone].badge}`}>{zoneData?.total_count || 0}</Badge>
                    {zone === 'STAT' && (
                      <Badge className="border bg-red-900/40 text-red-300 border-red-700/40">
                        Overdue {zoneData?.overdue_count || 0}
                      </Badge>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                {preview.length === 0 ? (
                  <p className="text-xs text-zinc-500">No items</p>
                ) : (
                  preview.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => setSelectedItem(item)}
                      className="w-full text-left rounded-md border border-zinc-800 bg-zinc-950/40 px-3 py-2 hover:bg-zinc-800/40"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm text-zinc-100 truncate">{item.subject}</span>
                        <span className="text-xs text-zinc-500">{Math.round(item.risk_score * 100)}%</span>
                      </div>
                      {item.patient_name && (
                        <p className="text-xs text-zinc-500 truncate">Patient: {item.patient_name}</p>
                      )}
                    </button>
                  ))
                )}
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Dialog open={!!selectedItem} onOpenChange={(open) => !open && setSelectedItem(null)}>
        <DialogContent className="bg-zinc-900 border-zinc-700 text-zinc-100">
          <DialogHeader>
            <DialogTitle>{selectedItem?.subject}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="text-sm text-zinc-300 whitespace-pre-wrap">{selectedItem?.snippet || 'No snippet available.'}</div>
            <div className="flex items-center justify-between text-xs text-zinc-500">
              <span>Risk: {Math.round((selectedItem?.risk_score || 0) * 100)}%</span>
              <span>State: {selectedItem?.lifecycle_state}</span>
            </div>
            <div className="flex justify-end">
              <Button onClick={handleEscalate} className="bg-red-700 hover:bg-red-600 text-white">
                Force Escalate
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
