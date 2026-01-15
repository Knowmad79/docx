import { useState, useEffect } from 'react'
import './App.css'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AlertTriangle, Clock, Calendar, Archive, Mail, Plus, LogOut, Zap, RefreshCw, Trash2, Bot, Check, Clock3, Copy, Send } from 'lucide-react'
import GridView from '@/components/GridView'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

type ZoneType = 'STAT' | 'TODAY' | 'THIS_WEEK' | 'LATER'

interface Message {
  id: string
  sender: string
  sender_domain: string
  subject: string
  snippet: string | null
  zone: ZoneType
  confidence: number
  reason: string
  jone5_message: string
  received_at: string
  classified_at: string
  corrected: boolean
  summary?: string | null
  recommended_action?: string | null
  action_type?: string | null
  draft_reply?: string | null
}

interface User {
  id: string
  email: string
  name: string
  practice_name?: string
}

interface EmailSource {
  id: string
  name: string
  inbound_token: string
  inbound_address: string
  created_at: string
  email_count: number
}

interface ZoneData {
  zones: Record<ZoneType, Message[]>
  counts: Record<ZoneType, number>
  total: number
}

interface ActionCenter {
  urgent_count: number
  needs_reply_count: number
  snoozed_due_count: number
  done_today: number
  total_action_items: number
  urgent_items: Message[]
  needs_reply: Message[]
  snoozed_due: Message[]
}

const zoneConfig: Record<ZoneType, { label: string; icon: React.ReactNode; color: string; pillBg: string }> = {
  STAT: { label: 'CRITICAL', icon: <AlertTriangle className="w-3 h-3" />, color: 'text-red-400', pillBg: 'bg-red-500/20 text-red-400 border-red-500/30' },
  TODAY: { label: 'HIGH', icon: <Clock className="w-3 h-3" />, color: 'text-orange-400', pillBg: 'bg-orange-500/20 text-orange-400 border-orange-500/30' },
  THIS_WEEK: { label: 'ROUTINE', icon: <Calendar className="w-3 h-3" />, color: 'text-blue-400', pillBg: 'bg-blue-500/20 text-blue-400 border-blue-500/30' },
  LATER: { label: 'FYI', icon: <Archive className="w-3 h-3" />, color: 'text-zinc-400', pillBg: 'bg-zinc-500/20 text-zinc-400 border-zinc-500/30' }
}

function App() {
  const [user, setUser] = useState<User | null>(null)
  const [token, setToken] = useState<string | null>(null)
  const [zoneData, setZoneData] = useState<ZoneData | null>(null)
  const [loading, setLoading] = useState(false)
  const [jone5Message, setJone5Message] = useState<string>('')
  const [isLoginMode, setIsLoginMode] = useState(true)
  const [loginForm, setLoginForm] = useState({ email: '', password: '', name: '', practice_name: '' })
  const [ingestForm, setIngestForm] = useState({ sender: '', subject: '', snippet: '' })
  const [ingestOpen, setIngestOpen] = useState(false)
  const [sourceOpen, setSourceOpen] = useState(false)
  const [sourceName, setSourceName] = useState('')
  const [createdSource, setCreatedSource] = useState<EmailSource | null>(null)
  const [selectedMessage, setSelectedMessage] = useState<Message | null>(null)
  const [newZone, setNewZone] = useState<ZoneType>('TODAY')
  const [actionCenter, setActionCenter] = useState<ActionCenter | null>(null)
  const [activeTab, setActiveTab] = useState<'all' | ZoneType>('all')
  const [viewMode, setViewMode] = useState<'grid' | 'inbox'>('grid')

  useEffect(() => {
    const savedToken = localStorage.getItem('docboxrx_token')
    const savedUser = localStorage.getItem('docboxrx_user')
    if (savedToken && savedUser) {
      setToken(savedToken)
      setUser(JSON.parse(savedUser))
    }
  }, [])

  useEffect(() => {
    if (token) {
      fetchMessages()
      fetchActionCenter()
    }
  }, [token])

  useEffect(() => {
    if (zoneData && !selectedMessage) {
      const first = zoneData.zones.STAT?.[0] || zoneData.zones.TODAY?.[0] || zoneData.zones.THIS_WEEK?.[0] || zoneData.zones.LATER?.[0]
      if (first) setSelectedMessage(first)
    }
  }, [zoneData])

  const allMessages = zoneData ? [
    ...zoneData.zones.STAT,
    ...zoneData.zones.TODAY,
    ...zoneData.zones.THIS_WEEK,
    ...zoneData.zones.LATER
  ] : []

  const filteredMessages = activeTab === 'all' ? allMessages : (zoneData?.zones[activeTab] || [])

  const fetchActionCenter = async () => {
    try {
      const data = await apiCall('/api/action-center')
      if (data) setActionCenter(data)
    } catch (error) {
      console.error('Failed to fetch action center:', error)
    }
  }

  const handleMarkDone = async (messageId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    try {
      await apiCall(`/api/messages/${messageId}/status`, { method: 'POST', body: JSON.stringify({ status: 'done' }) })
      setJone5Message("Done! One less thing to worry about.")
      fetchMessages()
      fetchActionCenter()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Failed to mark as done')
    }
  }

  const handleArchive = async (messageId: string, e?: React.MouseEvent) => {
    e?.stopPropagation()
    try {
      await apiCall(`/api/messages/${messageId}/status`, { method: 'POST', body: JSON.stringify({ status: 'archived' }) })
      setJone5Message("Archived!")
      fetchMessages()
      fetchActionCenter()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Failed to archive')
    }
  }

  const handleSnooze = async (messageId: string, hours: number, e?: React.MouseEvent) => {
    e?.stopPropagation()
    const snoozedUntil = new Date(Date.now() + hours * 60 * 60 * 1000).toISOString()
    try {
      await apiCall(`/api/messages/${messageId}/status`, { method: 'POST', body: JSON.stringify({ status: 'snoozed', snoozed_until: snoozedUntil }) })
      setJone5Message(`Snoozed for ${hours} hours.`)
      fetchMessages()
      fetchActionCenter()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Failed to snooze')
    }
  }

  const apiCall= async (endpoint: string, options: RequestInit = {}) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 30000)
    try {
      const response = await fetch(`${API_URL}${endpoint}`, { ...options, headers, signal: controller.signal })
      clearTimeout(timeoutId)
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Request failed' }))
        if (response.status === 401 && token) {
          localStorage.removeItem('docboxrx_token')
          localStorage.removeItem('docboxrx_user')
          setToken(null)
          setUser(null)
          setZoneData(null)
          alert('Session expired. Please log in again.')
          return null
        }
        throw new Error(error.detail || 'Request failed')
      }
      return response.json()
    } catch (error) {
      clearTimeout(timeoutId)
      if (error instanceof Error && error.name === 'AbortError') {
        throw new Error('Request timed out. Please try again.')
      }
      throw error
    }
  }

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const endpoint = isLoginMode ? '/api/auth/login' : '/api/auth/register'
      const body = isLoginMode ? { email: loginForm.email, password: loginForm.password } : loginForm
      const data = await apiCall(endpoint, { method: 'POST', body: JSON.stringify(body) })
      setToken(data.access_token)
      setUser(data.user)
      localStorage.setItem('docboxrx_token', data.access_token)
      localStorage.setItem('docboxrx_user', JSON.stringify(data.user))
      setJone5Message("Welcome! jonE5 is ready.")
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  const handleLogout = () => {
    setToken(null)
    setUser(null)
    setZoneData(null)
    localStorage.removeItem('docboxrx_token')
    localStorage.removeItem('docboxrx_user')
  }

  const fetchMessages = async () => {
    setLoading(true)
    try {
      const data = await apiCall('/api/messages/by-zone')
      setZoneData(data)
    } catch (error) {
      console.error('Failed to fetch messages:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleIngest = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const result = await apiCall('/api/messages/ingest', {
        method: 'POST',
        body: JSON.stringify({ sender: ingestForm.sender, subject: ingestForm.subject, body_plain: ingestForm.snippet }),
      })
      setJone5Message(result.jone5_message)
      setIngestForm({ sender: '', subject: '', snippet: '' })
      setIngestOpen(false)
      fetchMessages()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Ingest failed')
    } finally {
      setLoading(false)
    }
  }

  const handleCorrection = async () => {
    if (!selectedMessage) return
    setLoading(true)
    try {
      const result = await apiCall('/api/messages/correct', { method: 'POST', body: JSON.stringify({ message_id: selectedMessage.id, new_zone: newZone }) })
      setJone5Message(result.jone5_response)
      fetchMessages()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Correction failed')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (messageId: string) => {
    if (!confirm('Delete this message?')) return
    try {
      await apiCall(`/api/messages/${messageId}`, { method: 'DELETE' })
      if (selectedMessage?.id === messageId) setSelectedMessage(null)
      fetchMessages()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Delete failed')
    }
  }

  const seedDemoData = async () => {
    setLoading(true)
    try {
      await apiCall('/api/demo/seed', { method: 'POST' })
      setJone5Message("Demo data loaded!")
      fetchMessages()
      fetchActionCenter()
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Seed failed')
    } finally {
      setLoading(false)
    }
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
    setJone5Message("Copied to clipboard!")
  }

  const createSource = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const data = await apiCall('/api/sources', { method: 'POST', body: JSON.stringify({ name: sourceName }) })
      setCreatedSource(data)
      setSourceName('')
      setJone5Message('Inbound address created.')
    } catch (error) {
      alert(error instanceof Error ? error.message : 'Create source failed')
    } finally {
      setLoading(false)
    }
  }

  // LOGIN SCREEN
  if (!user) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center p-4">
        <Card className="w-full max-w-md bg-zinc-900 border-zinc-800">
          <CardHeader className="text-center pb-4">
            <div className="w-16 h-16 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <Bot className="w-9 h-9 text-white" />
            </div>
                        <CardTitle className="text-2xl font-bold text-zinc-100">DocBoxRX</CardTitle>
                        <CardDescription className="text-zinc-500">Smart Email Assistant</CardDescription>
                        <p className="text-xs text-emerald-500 mt-1">Powered by jonE5 AI Agent</p>
          </CardHeader>
          <CardContent>
            <Tabs value={isLoginMode ? 'login' : 'register'} onValueChange={(v) => setIsLoginMode(v === 'login')}>
              <TabsList className="grid w-full grid-cols-2 mb-6 bg-zinc-800">
                <TabsTrigger value="login" className="data-[state=active]:bg-emerald-600 text-zinc-400 data-[state=active]:text-white">Login</TabsTrigger>
                <TabsTrigger value="register" className="data-[state=active]:bg-emerald-600 text-zinc-400 data-[state=active]:text-white">Register</TabsTrigger>
              </TabsList>
              <form onSubmit={handleLogin} className="space-y-4">
                {!isLoginMode && (
                  <>
                    <div><Label className="text-zinc-400 text-sm">Name</Label><Input placeholder="Dr. Smith" value={loginForm.name} onChange={(e) => setLoginForm({ ...loginForm, name: e.target.value })} required={!isLoginMode} className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                    <div><Label className="text-zinc-400 text-sm">Practice</Label><Input placeholder="Smith Dental" value={loginForm.practice_name} onChange={(e) => setLoginForm({ ...loginForm, practice_name: e.target.value })} className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                  </>
                )}
                <div><Label className="text-zinc-400 text-sm">Email</Label><Input type="email" placeholder="doctor@practice.com" value={loginForm.email} onChange={(e) => setLoginForm({ ...loginForm, email: e.target.value })} required className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                <div><Label className="text-zinc-400 text-sm">Password</Label><Input type="password" placeholder="********" value={loginForm.password} onChange={(e) => setLoginForm({ ...loginForm, password: e.target.value })} required className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                <Button type="submit" className="w-full bg-emerald-600 hover:bg-emerald-700 text-white" disabled={loading}>{loading ? 'Please wait...' : (isLoginMode ? 'Login' : 'Create Account')}</Button>
              </form>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    )
  }

  // MAIN APP - TWO PANE LAYOUT
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex flex-col">
      {/* Header */}
      <header className="bg-zinc-900 border-b border-zinc-800 px-4 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gradient-to-br from-emerald-500 to-teal-600 rounded-lg flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-zinc-100">DocBoxRX</h1>
            <p className="text-xs text-zinc-500">{user.name}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={seedDemoData} disabled={loading} className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"><Zap className="w-4 h-4 mr-1" />Demo</Button>
          <Dialog open={sourceOpen} onOpenChange={(open) => { setSourceOpen(open); if (!open) setCreatedSource(null) }}>
            <DialogTrigger asChild><Button size="sm" className="bg-emerald-600 hover:bg-emerald-700"><Plus className="w-4 h-4 mr-1" />Add Inbox</Button></DialogTrigger>
            <DialogContent className="bg-zinc-900 border-zinc-700 text-zinc-100">
              <DialogHeader>
                <DialogTitle>Add Inbox Address</DialogTitle>
                <DialogDescription className="text-zinc-400">Create a unique inbound address for DocBoxRX</DialogDescription>
              </DialogHeader>
              {createdSource ? (
                <div className="space-y-3">
                  <div>
                    <Label className="text-zinc-400">Inbound Address</Label>
                    <div className="flex gap-2 mt-1">
                      <Input value={createdSource.inbound_address} readOnly className="bg-zinc-800 border-zinc-700 text-zinc-100" />
                      <Button type="button" variant="outline" onClick={() => copyToClipboard(createdSource.inbound_address)} className="bg-zinc-800 border-zinc-700 text-zinc-300">Copy</Button>
                    </div>
                  </div>
                  <p className="text-xs text-zinc-500">Forward or send email to this address to add it to DocBoxRX.</p>
                </div>
              ) : (
                <form onSubmit={createSource} className="space-y-4">
                  <div>
                    <Label className="text-zinc-400">Inbox Name</Label>
                    <Input placeholder="Front Desk Inbox" value={sourceName} onChange={(e) => setSourceName(e.target.value)} required className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" />
                  </div>
                  <Button type="submit" className="w-full bg-emerald-600 hover:bg-emerald-700" disabled={loading}>
                    {loading ? 'Creating...' : 'Create Inbound Address'}
                  </Button>
                </form>
              )}
            </DialogContent>
          </Dialog>
          <Dialog open={ingestOpen} onOpenChange={setIngestOpen}>
            <DialogTrigger asChild><Button size="sm" variant="outline" className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"><Plus className="w-4 h-4 mr-1" />Paste Email</Button></DialogTrigger>
            <DialogContent className="bg-zinc-900 border-zinc-700 text-zinc-100">
              <DialogHeader><DialogTitle>Paste Email</DialogTitle><DialogDescription className="text-zinc-400">Paste email details for jonE5 to analyze</DialogDescription></DialogHeader>
              <form onSubmit={handleIngest} className="space-y-4">
                <div><Label className="text-zinc-400">From</Label><Input placeholder="sender@example.com" value={ingestForm.sender} onChange={(e) => setIngestForm({ ...ingestForm, sender: e.target.value })} required className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                <div><Label className="text-zinc-400">Subject</Label><Input placeholder="Email subject" value={ingestForm.subject} onChange={(e) => setIngestForm({ ...ingestForm, subject: e.target.value })} required className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                <div><Label className="text-zinc-400">Body</Label><Textarea placeholder="Full email content..." value={ingestForm.snippet} onChange={(e) => setIngestForm({ ...ingestForm, snippet: e.target.value })} rows={5} className="bg-zinc-800 border-zinc-700 text-zinc-100 mt-1" /></div>
                <Button type="submit" className="w-full bg-emerald-600 hover:bg-emerald-700" disabled={loading}>{loading ? 'Analyzing...' : 'Analyze with jonE5'}</Button>
              </form>
            </DialogContent>
          </Dialog>
          <Button variant="outline" size="sm" onClick={fetchMessages} disabled={loading} className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700"><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /></Button>
          <Button variant="ghost" size="sm" onClick={handleLogout} className="text-zinc-400 hover:text-zinc-100"><LogOut className="w-4 h-4" /></Button>
        </div>
      </header>

      {/* jonE5 Message Bar */}
      {jone5Message && (
        <div className="bg-emerald-900/30 border-b border-emerald-800/50 px-4 py-2 flex items-center gap-2">
          <Bot className="w-4 h-4 text-emerald-400" />
          <span className="text-sm text-emerald-300">{jone5Message}</span>
          <Button variant="ghost" size="sm" className="ml-auto h-6 px-2 text-emerald-400 hover:text-emerald-300" onClick={() => setJone5Message('')}>x</Button>
        </div>
      )}

      {/* Action Center Summary */}
      {actionCenter && actionCenter.total_action_items > 0 && (
        <div className="bg-zinc-900/50 border-b border-zinc-800 px-4 py-3">
          <div className="flex items-center gap-6 text-sm">
            <span className="text-zinc-400">Today:</span>
            <span className="text-red-400 font-medium">{actionCenter.urgent_count} urgent</span>
            <span className="text-orange-400 font-medium">{actionCenter.needs_reply_count} need reply</span>
            <span className="text-emerald-400 font-medium">{actionCenter.done_today} done</span>
          </div>
        </div>
      )}

      {/* View Mode Tabs */}
      <div className="border-b border-zinc-800 bg-zinc-900/40 px-4 py-2 flex gap-2">
        <Button variant={viewMode === 'grid' ? 'default' : 'ghost'} size="sm" onClick={() => setViewMode('grid')} className={viewMode === 'grid' ? 'bg-zinc-700' : 'text-zinc-400'}>
          Decision Deck
        </Button>
        <Button variant={viewMode === 'inbox' ? 'default' : 'ghost'} size="sm" onClick={() => setViewMode('inbox')} className={viewMode === 'inbox' ? 'bg-zinc-700' : 'text-zinc-400'}>
          Inbox
        </Button>
      </div>

      {viewMode === 'grid' ? (
        <div className="flex-1 overflow-y-auto">
          <GridView apiCall={apiCall} owner="lead_doctor" onNotify={setJone5Message} />
        </div>
      ) : (
        /* Main Content - Two Pane */
        <div className="flex-1 flex overflow-hidden">
          {/* Left Pane - Message List */}
          <div className="w-96 border-r border-zinc-800 flex flex-col bg-zinc-900/50">
            {/* Filter Tabs */}
            <div className="p-2 border-b border-zinc-800 flex gap-1 overflow-x-auto">
              <Button variant={activeTab === 'all' ? 'default' : 'ghost'} size="sm" onClick={() => setActiveTab('all')} className={activeTab === 'all' ? 'bg-zinc-700' : 'text-zinc-400'}>All ({allMessages.length})</Button>
              {(['STAT', 'TODAY', 'THIS_WEEK', 'LATER'] as ZoneType[]).map(z => (
                <Button key={z} variant={activeTab === z ? 'default' : 'ghost'} size="sm" onClick={() => setActiveTab(z)} className={`${activeTab === z ? 'bg-zinc-700' : 'text-zinc-400'} ${zoneConfig[z].color}`}>
                  {zoneConfig[z].label} ({zoneData?.counts[z] || 0})
                </Button>
              ))}
            </div>
            {/* Message List */}
            <div className="flex-1 overflow-y-auto">
              {filteredMessages.length === 0 ? (
                <div className="p-8 text-center text-zinc-500">
                  <Mail className="w-12 h-12 mx-auto mb-3 opacity-30" />
                  <p>No messages</p>
                  <p className="text-xs mt-1">Click Demo to load sample emails</p>
                </div>
              ) : (
                filteredMessages.map((msg) => (
                  <div key={msg.id} onClick={() => setSelectedMessage(msg)} className={`p-3 border-b border-zinc-800 cursor-pointer hover:bg-zinc-800/50 transition-colors ${selectedMessage?.id === msg.id ? 'bg-zinc-800 border-l-2 border-l-emerald-500' : ''}`}>
                    <div className="flex items-center gap-2 mb-1">
                      <Badge className={`text-xs px-1.5 py-0 border ${zoneConfig[msg.zone].pillBg}`}>{zoneConfig[msg.zone].label}</Badge>
                      <span className="text-xs text-zinc-500 truncate flex-1">{msg.sender}</span>
                      <span className="text-xs text-zinc-600">{Math.round(msg.confidence * 100)}%</span>
                    </div>
                    <p className="text-sm font-medium text-zinc-200 truncate">{msg.subject}</p>
                    {msg.recommended_action && <p className="text-xs text-emerald-500 mt-1 truncate">{msg.recommended_action}</p>}
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Right Pane - Email Detail + jonE5 Analysis */}
          <div className="flex-1 flex flex-col overflow-hidden">
            {selectedMessage ? (
            <>
              {/* Email Header */}
              <div className="p-4 border-b border-zinc-800 bg-zinc-900/30">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2">
                      <Badge className={`border ${zoneConfig[selectedMessage.zone].pillBg}`}>{zoneConfig[selectedMessage.zone].icon}{zoneConfig[selectedMessage.zone].label}</Badge>
                      <span className="text-xs text-zinc-500">{Math.round(selectedMessage.confidence * 100)}% confidence</span>
                    </div>
                    <h2 className="text-xl font-semibold text-zinc-100 mb-1">{selectedMessage.subject}</h2>
                    <p className="text-sm text-zinc-400">From: <span className="text-zinc-300">{selectedMessage.sender}</span></p>
                    <p className="text-xs text-zinc-500 mt-1">{new Date(selectedMessage.received_at).toLocaleString()}</p>
                  </div>
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" onClick={(e) => handleMarkDone(selectedMessage.id, e)} className="bg-zinc-800 border-zinc-700 text-emerald-400 hover:bg-emerald-900/30"><Check className="w-4 h-4" /></Button>
                    <Button size="sm" variant="outline" onClick={(e) => handleSnooze(selectedMessage.id, 4, e)} className="bg-zinc-800 border-zinc-700 text-blue-400 hover:bg-blue-900/30"><Clock3 className="w-4 h-4" /></Button>
                    <Button size="sm" variant="outline" onClick={(e) => handleArchive(selectedMessage.id, e)} className="bg-zinc-800 border-zinc-700 text-zinc-400 hover:bg-zinc-700"><Archive className="w-4 h-4" /></Button>
                    <Button size="sm" variant="outline" onClick={() => handleDelete(selectedMessage.id)} className="bg-zinc-800 border-zinc-700 text-red-400 hover:bg-red-900/30"><Trash2 className="w-4 h-4" /></Button>
                  </div>
                </div>
              </div>

              {/* Scrollable Content */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {/* Email Body */}
                <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-3">Email Content</h3>
                  <div className="text-sm text-zinc-300 whitespace-pre-wrap leading-relaxed">
                    {selectedMessage.snippet || 'No email body available. The email content will appear here when synced from your email provider or when you paste the full email content.'}
                  </div>
                </div>

                {/* jonE5 AI Analysis */}
                <div className="bg-gradient-to-br from-emerald-950/50 to-teal-950/30 border border-emerald-800/50 rounded-lg p-4">
                  <div className="flex items-center gap-2 mb-4">
                    <Bot className="w-5 h-5 text-emerald-400" />
                    <h3 className="text-sm font-semibold text-emerald-400">jonE5 AI Analysis</h3>
                    <span className="text-xs text-emerald-600 bg-emerald-900/50 px-2 py-0.5 rounded">AI Generated</span>
                  </div>

                  {/* Summary */}
                  <div className="mb-4">
                    <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Summary</h4>
                    <p className="text-sm text-zinc-300">{selectedMessage.summary || selectedMessage.reason || 'jonE5 analyzed this email and classified it based on sender patterns and content keywords.'}</p>
                  </div>

                  {/* Recommended Action */}
                  <div className="mb-4">
                    <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Recommended Action</h4>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-emerald-300">{selectedMessage.recommended_action || 'Review and respond as needed'}</span>
                      {selectedMessage.action_type && <Badge className="bg-emerald-900/50 text-emerald-400 border-emerald-700">{selectedMessage.action_type}</Badge>}
                    </div>
                  </div>

                  {/* Classification Reason */}
                  <div className="mb-4">
                    <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Why This Priority</h4>
                    <p className="text-sm text-zinc-400">{selectedMessage.reason}</p>
                  </div>

                  {/* Draft Reply */}
                  <div>
                    <h4 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">Draft Reply</h4>
                    <div className="bg-zinc-900/80 border border-zinc-700 rounded-lg p-3">
                      <p className="text-sm text-zinc-300 whitespace-pre-wrap mb-3">
                        {selectedMessage.draft_reply || `Thank you for your email regarding "${selectedMessage.subject}".\n\nI have reviewed the information and will respond accordingly.\n\nBest regards,\n${user.name}`}
                      </p>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => copyToClipboard(selectedMessage.draft_reply || `Thank you for your email regarding "${selectedMessage.subject}".\n\nI have reviewed the information and will respond accordingly.\n\nBest regards,\n${user.name}`)} className="bg-zinc-800 border-zinc-700 text-zinc-300 hover:bg-zinc-700">
                          <Copy className="w-4 h-4 mr-1" />Copy Reply
                        </Button>
                        <Button size="sm" onClick={() => {
                          const senderEmail = selectedMessage.sender.match(/<([^>]+)>/)?.[1] || selectedMessage.sender.match(/[\w.-]+@[\w.-]+/)?.[0] || selectedMessage.sender;
                          const subject = `Re: ${selectedMessage.subject}`;
                          const body = selectedMessage.draft_reply || `Thank you for your email regarding "${selectedMessage.subject}".\n\nI have reviewed the information and will respond accordingly.\n\nBest regards,\n${user.name}`;
                          window.open(`mailto:${encodeURIComponent(senderEmail)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`, '_blank');
                        }} className="bg-emerald-700 hover:bg-emerald-600 text-white">
                          <Send className="w-4 h-4 mr-1" />Send Reply
                        </Button>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Move to Different Zone */}
                <div className="bg-zinc-900 border border-zinc-800 rounded-lg p-4">
                  <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-3">Reclassify</h3>
                  <div className="flex items-center gap-2">
                    <Select value={newZone} onValueChange={(v) => setNewZone(v as ZoneType)}>
                      <SelectTrigger className="w-40 bg-zinc-800 border-zinc-700 text-zinc-300"><SelectValue /></SelectTrigger>
                      <SelectContent className="bg-zinc-800 border-zinc-700">
                        {(['STAT', 'TODAY', 'THIS_WEEK', 'LATER'] as ZoneType[]).map((z) => (
                          <SelectItem key={z} value={z} className="text-zinc-300">{zoneConfig[z].label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button size="sm" onClick={handleCorrection} disabled={loading || newZone === selectedMessage.zone} className="bg-zinc-700 hover:bg-zinc-600 text-zinc-200">Move & Teach jonE5</Button>
                  </div>
                </div>
              </div>
            </>
            ) : (
            <div className="flex-1 flex items-center justify-center text-zinc-500">
              <div className="text-center">
                <Mail className="w-16 h-16 mx-auto mb-4 opacity-30" />
                <p className="text-lg">Select an email to view</p>
                <p className="text-sm mt-1">Or click Demo to load sample emails</p>
              </div>
            </div>
          )}
          </div>
        </div>
      )}
    </div>
  )
}

export default App
